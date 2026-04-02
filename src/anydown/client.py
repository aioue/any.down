"""
Any.do API Client

A Python client for the Any.do API with session persistence, 2FA support,
and efficient sync strategies.
"""

import hashlib
import json
import logging
import os
import sys
import textwrap
import time
import uuid
from datetime import datetime
from typing import Any, TypedDict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

__all__ = ["AnyDoClient", "TaskInfo", "ListInfo", "ExportInfo", "send_ntfy"]


def send_ntfy(ntfy_config: dict[str, Any] | None, title: str, message: str, priority: int = 3, tags: list[str] | None = None) -> bool:
    """
    Send a notification via ntfy.sh.

    Args:
        ntfy_config: Configuration dict with 'enabled', 'url', 'topic', 'token' keys
        title: Notification title
        message: Notification message body
        priority: 1-5, where 5 is highest (default: 3)
        tags: Optional list of emoji tags

    Returns:
        True if sent successfully, False otherwise or if ntfy is not configured/enabled
    """
    if not ntfy_config or not ntfy_config.get("enabled"):
        return False

    try:
        url = ntfy_config.get("url", "https://ntfy.sh")
        topic = ntfy_config.get("topic", "anydo-alerts")
        token = ntfy_config.get("token")
        notification_url = f"{url}/{topic}"

        headers = {
            "Title": title,
            "Priority": str(max(1, min(5, priority))),
        }

        if tags:
            headers["Tags"] = ",".join(tags)

        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = requests.post(notification_url, data=message, headers=headers, timeout=10)
        if response.status_code == 200:
            logger.debug("ntfy notification sent successfully")
            return True

        logger.warning("ntfy notification failed with status %d", response.status_code)
        return False

    except requests.RequestException as e:
        logger.warning("Error sending ntfy notification: %s", e)
        return False
    except Exception as e:
        logger.warning("Unexpected error sending ntfy: %s", e)
        return False


def _anydo_stdin_interactive() -> bool:
    """
    True if 2FA can be completed via prompts (real terminal).

    ANYDO_NON_INTERACTIVE=1 forces False (e.g. cron, Docker without TTY).
    ANYDO_FORCE_INTERACTIVE=1 forces True (e.g. tests).
    """
    if os.environ.get("ANYDO_FORCE_INTERACTIVE", "").lower() in ("1", "true", "yes"):
        return True
    if os.environ.get("ANYDO_NON_INTERACTIVE", "").lower() in ("1", "true", "yes"):
        return False
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


# =============================================================================
# Constants
# =============================================================================


class APIConstants:
    """API-related constants."""

    BASE_URL = "https://sm-prod4.any.do"
    API_VERSION = "5.0.97"
    PLATFORM = "web"
    X_PLATFORM = "3"
    REQUESTED_EXPERIMENTS = [
        "AI_FEATURES",
        "MAC_IN_REVIEW",
        "WEB_LOCALIZED_PRICING_FEB23",
        "WEB_OB_AI_MAR_24",
        "WEB_PREMIUM_TRIAL",
        "WEB_CALENDAR_QUOTA",
    ]


class SyncConstants:
    """Sync-related constants."""

    FULL_SYNC_RATE_LIMIT_MS = 60000  # 60 seconds
    MAX_POLL_WAIT_FULL_SYNC = 15  # seconds
    MAX_POLL_WAIT_INCREMENTAL = 10  # seconds
    INITIAL_POLL_INTERVAL = 0.5  # seconds
    MAX_POLL_INTERVAL = 2.0  # seconds
    POLL_BACKOFF_MULTIPLIER = 1.5


class AuthConstants:
    """Authentication-related constants."""

    MAX_2FA_ATTEMPTS = 3
    LOGIN_DELAY_SECONDS = 2
    VERIFY_DELAY_SECONDS = 1
    REQUEST_TIMEOUT = 30  # seconds
    SESSION_TEST_TIMEOUT = 10  # seconds


class RetryConstants:
    """Retry-related constants."""

    MAX_RETRIES = 3
    BACKOFF_FACTOR = 1
    STATUS_FORCELIST = [429, 500, 502, 503, 504]


# =============================================================================
# Type Definitions
# =============================================================================


class TaskInfo(TypedDict, total=False):
    """Type definition for task information."""

    title: str
    id: str | None
    parent_id: str | None
    created_date: str
    last_update: str
    due_date: str
    list_name: str
    note: str
    tags: list[str]
    status: str
    priority: str
    list_color: str | None
    assignee: str | None
    repeating: str
    subtasks: list["TaskInfo"]
    _internal_status: str


class ListInfo(TypedDict, total=False):
    """Type definition for list/category information."""

    id: str
    name: str
    color: str | None
    is_default: bool
    position: int | None
    is_deleted: bool
    task_count: int
    pending_count: int
    completed_count: int


class ExportInfo(TypedDict, total=False):
    """Type definition for export metadata."""

    extracted_at: str
    total_tasks: int
    pending_tasks: int
    completed_tasks: int
    error: str


# =============================================================================
# Main Client Class
# =============================================================================


class AnyDoClient:
    """
    A Python client for the Any.do API.

    This client handles authentication, session persistence, and provides methods
    to interact with your Any.do tasks and lists.

    Example:
        >>> client = AnyDoClient()
        >>> client.login("email@example.com", "password")
        >>> tasks = client.get_tasks()
        >>> client.print_tasks_summary(tasks)
    """

    def __init__(self, session_file: str = "session.json", text_wrap_width: int = 80, rotate_client_id: bool = False):
        self.session = requests.Session()
        self.base_url = APIConstants.BASE_URL
        self.logged_in = False
        self.user_info: dict[str, Any] | None = None
        self.session_file = session_file
        self.last_data_hash: str | None = None
        self.last_pretty_hash: str | None = None
        self.text_wrap_width = text_wrap_width
        self.last_sync_timestamp: int | None = None
        self.last_full_sync_timestamp: int | None = None
        self.client_id = str(uuid.uuid4())
        self.rotate_client_id = rotate_client_id
        self.auth_token: str | None = None

        retry_strategy = Retry(
            total=RetryConstants.MAX_RETRIES,
            backoff_factor=RetryConstants.BACKOFF_FACTOR,
            status_forcelist=RetryConstants.STATUS_FORCELIST,
            allowed_methods=["HEAD", "GET", "OPTIONS"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,pl;q=0.7,no;q=0.6",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Content-Type": "application/json; charset=UTF-8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
                "X-Anydo-Platform": APIConstants.PLATFORM,
                "X-Anydo-Version": APIConstants.API_VERSION,
                "X-Platform": APIConstants.X_PLATFORM,
            }
        )

        self._load_session()

    # -------------------------------------------------------------------------
    # Session management
    # -------------------------------------------------------------------------

    def _load_session(self) -> bool:
        """Load existing session from file if available."""
        if not os.path.exists(self.session_file):
            return False

        try:
            with open(self.session_file) as f:
                session_data = json.load(f)

            for cookie_data in session_data.get("cookies", []):
                self.session.cookies.set(
                    cookie_data["name"],
                    cookie_data["value"],
                    domain=cookie_data.get("domain"),
                    path=cookie_data.get("path", "/"),
                )

            self.user_info = session_data.get("user_info")
            self.last_data_hash = session_data.get("last_data_hash")
            self.last_pretty_hash = session_data.get("last_pretty_hash")
            self.last_sync_timestamp = session_data.get("last_sync_timestamp")
            self.last_full_sync_timestamp = session_data.get("last_full_sync_timestamp")
            if session_data.get("client_id") and not self.rotate_client_id:
                self.client_id = session_data["client_id"]

            user_email = self.user_info.get("email", "unknown user") if self.user_info else "unknown user"
            logger.info("Loaded existing session for %s", user_email)

            if self._test_session():
                self.logged_in = True
                logger.info("Session is still valid")
                return True
            logger.warning("Session expired, will need to login again")
            self._clear_session()
            return False

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Error loading session: %s", e)
            self._clear_session()
            return False
        except OSError as e:
            logger.warning("Error reading session file: %s", e)
            self._clear_session()
            return False

    def _save_session(self) -> None:
        """Save current session to file."""
        try:
            session_data = {
                "cookies": [
                    {"name": cookie.name, "value": cookie.value, "domain": cookie.domain, "path": cookie.path}
                    for cookie in self.session.cookies
                ],
                "user_info": self.user_info,
                "saved_at": datetime.now().isoformat(),
                "client_id": self.client_id,
                "last_data_hash": self.last_data_hash,
                "last_pretty_hash": self.last_pretty_hash,
                "last_sync_timestamp": self.last_sync_timestamp,
                "last_full_sync_timestamp": self.last_full_sync_timestamp,
            }

            with open(self.session_file, "w") as f:
                json.dump(session_data, f, indent=2)

            logger.info("Session saved successfully")

        except (OSError, TypeError) as e:
            logger.error("Error saving session: %s", e)

    def _clear_session(self) -> None:
        """Clear session data."""
        self.session.cookies.clear()
        self.user_info = None
        self.logged_in = False
        if os.path.exists(self.session_file):
            try:
                os.remove(self.session_file)
            except OSError as e:
                logger.debug("Could not remove session file: %s", e)

    def _test_session(self) -> bool:
        """Test if current session is still valid."""
        try:
            user_url = f"{self.base_url}/me"
            response = self.session.get(user_url, timeout=AuthConstants.SESSION_TEST_TIMEOUT)
            return response.status_code == 200
        except requests.RequestException as e:
            logger.debug("Session test failed: %s", e)
            return False

    # -------------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------------

    def login(self, email: str, password: str) -> bool:
        """
        Login to Any.do with email and password.

        Returns:
            True if login successful, False otherwise
        """
        if self.logged_in and self._test_session():
            logger.info("Already logged in with valid session")
            return True

        if not _anydo_stdin_interactive():
            logger.error(
                "Non-interactive environment: cannot complete Any.do login (2FA needs a terminal). "
                "Fix %s (valid JSON and working cookies), or run `anydown` once locally. "
                "See README for manual session export.",
                self.session_file,
            )
            return False

        try:
            logger.info("Checking email...")
            check_email_url = f"{self.base_url}/check_email"

            time.sleep(AuthConstants.LOGIN_DELAY_SECONDS)
            response = self.session.post(check_email_url, json={"email": email}, timeout=AuthConstants.REQUEST_TIMEOUT)

            if response.status_code == 200:
                email_data = response.json()
                if not email_data.get("user_exists", False):
                    logger.warning("Email not found in system")
                    return False
                logger.info("Email found in system")
            else:
                logger.warning("Email check failed: %d, continuing...", response.status_code)

            logger.info("Attempting 2FA login flow...")
            if self._trigger_2fa_email(email, password):
                return self._handle_2fa_interactive(email, password)
            logger.error("Failed to trigger 2FA email")
            return False

        except requests.RequestException as e:
            logger.error("Login error: %s", e)
            return False

    def _handle_2fa_interactive(self, email: str, password: str) -> bool:
        """Handle 2FA verification with interactive prompts."""
        print("\n🔐 2FA verification required. Check your email for the code.")

        for attempt in range(AuthConstants.MAX_2FA_ATTEMPTS):
            try:
                code = input("Enter 6-digit code: ").strip()

                if not code:
                    print("No code entered.")
                    continue

                if len(code) != 6 or not code.isdigit():
                    print("Invalid format. Enter 6 digits.")
                    continue

                if self._verify_2fa_code(email, password, code):
                    self.logged_in = True
                    self._get_user_info()
                    self._save_session()
                    return True
                remaining = AuthConstants.MAX_2FA_ATTEMPTS - 1 - attempt
                if remaining > 0:
                    print(f"Invalid code. {remaining} attempts left.")

            except KeyboardInterrupt:
                print("\nCancelled.")
                return False
            except EOFError:
                logger.error("Cannot read 2FA code (non-interactive stdin).")
                return False

        print("Too many failed attempts.")
        return False

    def _build_auth_payload(self, email: str, password: str, **extra: Any) -> dict[str, Any]:
        """Build the common auth payload used by 2FA endpoints."""
        payload: dict[str, Any] = {
            "platform": APIConstants.PLATFORM,
            "referrer": "",
            "requested_experiments": APIConstants.REQUESTED_EXPERIMENTS,
            "create_predefined_data": {"lists": True, "label": True},
            "client_id": self.client_id,
            "locale": "en",
            "email": email,
            "password": password,
        }
        payload.update(extra)
        return payload

    def _trigger_2fa_email(self, email: str, password: str) -> bool:
        """Trigger 2FA email to be sent using the /login-2fa endpoint."""
        try:
            logger.info("Triggering 2FA email...")

            login_2fa_url = f"{self.base_url}/login-2fa"
            payload = self._build_auth_payload(email, password)

            time.sleep(AuthConstants.LOGIN_DELAY_SECONDS)
            response = self.session.post(login_2fa_url, json=payload, timeout=AuthConstants.REQUEST_TIMEOUT)

            if response.status_code == 200:
                logger.info("2FA email triggered successfully")
                return True
            if response.status_code == 403:
                logger.error(
                    "2FA email trigger returned 403 Forbidden. Any.do did not send the "
                    "verification email. This may be rate limiting, IP blocking, or bot detection. "
                    "Try again in 10-30 minutes, or use a different network (e.g. home vs VPS)."
                )
                logger.debug("Response body: %s", response.text[:500] if response.text else "(empty)")
                return False
            logger.error(
                "2FA email trigger failed with status %d. The verification email was likely not sent.",
                response.status_code,
            )
            logger.debug("Response body: %s", response.text[:500] if response.text else "(empty)")
            return False

        except requests.RequestException as e:
            logger.error("Error triggering 2FA email: %s", e)
            return False

    def _verify_2fa_code(self, email: str, password: str, code: str) -> bool:
        """Verify 2FA code with Any.do servers."""
        try:
            verify_url = f"{self.base_url}/login-2fa-code"
            payload = self._build_auth_payload(email, password, code=code)

            time.sleep(AuthConstants.VERIFY_DELAY_SECONDS)
            response = self.session.post(verify_url, json=payload, timeout=AuthConstants.REQUEST_TIMEOUT)

            if response.status_code != 200:
                logger.error("2FA verification failed with status: %d", response.status_code)
                return False

            try:
                response_data = response.json()
                if "auth_token" in response_data:
                    self.auth_token = response_data["auth_token"]
                    self.session.headers["X-Anydo-Auth"] = self.auth_token
                    logger.info("2FA verification successful")
                    return True
                logger.error("2FA verification failed - no auth token in response")
                return False
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Error parsing 2FA response: %s", e)
                auth_token = response.headers.get("X-Anydo-Auth")
                if auth_token:
                    logger.info("Found auth token in response headers")
                    self.auth_token = auth_token
                    self.session.headers["X-Anydo-Auth"] = auth_token
                    return True
                logger.error("No auth token found in headers either")
                return False

        except requests.RequestException as e:
            logger.error("Error verifying 2FA code: %s", e)
            return False

    def _get_user_info(self) -> bool:
        """Get user information after login."""
        try:
            user_url = f"{self.base_url}/me"
            response = self.session.get(user_url, timeout=AuthConstants.REQUEST_TIMEOUT)

            if response.status_code == 200:
                self.user_info = response.json()
                user_email = self.user_info.get("email", "Unknown")
                logger.info("Logged in as: %s", user_email)
                self._update_timezone()
                return True
            logger.warning("Failed to get user info: %d", response.status_code)
            return False

        except requests.RequestException as e:
            logger.error("Error getting user info: %s", e)
            return False

    def _update_timezone(self) -> None:
        """Update user timezone. Uses IANA timezone from the system via zoneinfo."""
        try:
            tz_override = os.environ.get("ANYDO_TIMEZONE")
            if tz_override:
                timezone_to_send = tz_override
            else:
                try:
                    local_tz = datetime.now().astimezone().tzinfo
                    tz_key = getattr(local_tz, "key", None)
                    timezone_to_send = tz_key or str(local_tz)
                except Exception:
                    timezone_to_send = "UTC"

            update_url = f"{self.base_url}/me"
            response = self.session.put(
                update_url, json={"timezone": timezone_to_send}, timeout=AuthConstants.REQUEST_TIMEOUT
            )
            if response.status_code == 200:
                logger.info("Timezone updated to: %s", timezone_to_send)
            else:
                logger.warning("Timezone update failed: %d", response.status_code)

        except requests.RequestException as e:
            logger.warning("Error updating timezone: %s", e)

    # -------------------------------------------------------------------------
    # Sync
    # -------------------------------------------------------------------------

    def _poll_for_result(self, task_id: str, max_wait: float) -> requests.Response | None:
        """
        Poll for a background sync result with exponential backoff.

        Returns the 200 response, or None on timeout.
        """
        poll_interval = SyncConstants.INITIAL_POLL_INTERVAL
        total_waited = 0.0
        result_url = f"{self.base_url}/me/bg_sync_result/{task_id}"

        while total_waited < max_wait:
            time.sleep(poll_interval)
            total_waited += poll_interval

            response = self.session.get(result_url, timeout=AuthConstants.REQUEST_TIMEOUT)

            if response.status_code == 200:
                return response
            if response.status_code == 202:
                poll_interval = min(
                    poll_interval * SyncConstants.POLL_BACKOFF_MULTIPLIER, SyncConstants.MAX_POLL_INTERVAL
                )
                continue
            response.raise_for_status()

        return None

    def get_tasks(self, include_completed: bool = False) -> dict[str, Any] | None:
        """
        Fetch tasks from Any.do using smart sync strategy.

        Uses incremental sync to detect changes, then performs full sync when changes
        are found (browser-like behavior). Falls back to full sync if incremental fails.
        """
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        if self.last_sync_timestamp:
            logger.info("Checking for changes with incremental sync...")
            incremental_data = self.get_tasks_incremental(include_completed)

            if incremental_data is None:
                logger.warning("Incremental sync failed, falling back to full sync...")
            elif self._has_meaningful_task_data(incremental_data):
                logger.info("Changes detected, performing full sync...")
                return self.get_tasks_full(include_completed)
            else:
                logger.info("No changes detected since last sync")
                return incremental_data

        logger.info("Performing full sync...")
        return self.get_tasks_full(include_completed)

    def get_tasks_incremental(self, include_completed: bool = False) -> dict[str, Any] | None:
        """Fetch only tasks updated since last sync."""
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        if not self.last_sync_timestamp:
            logger.warning("No last sync timestamp available")
            return None

        try:
            sync_url = f"{self.base_url}/api/v14/me/bg_sync"
            params = {"updatedSince": self.last_sync_timestamp, "includeNonVisible": "false"}

            last_sync_time = datetime.fromtimestamp(self.last_sync_timestamp / 1000).strftime("%Y-%m-%d %H:%M:%S")
            logger.info("Requesting changes since: %s", last_sync_time)

            sync_response = self.session.get(sync_url, params=params, timeout=AuthConstants.REQUEST_TIMEOUT)
            sync_response.raise_for_status()

            task_id = sync_response.json().get("task_id")
            if not task_id:
                logger.error("Could not get sync task ID for incremental sync")
                return None

            result_response = self._poll_for_result(task_id, SyncConstants.MAX_POLL_WAIT_INCREMENTAL)
            if result_response is None:
                logger.warning("Incremental sync operation timed out")
                return None

            tasks_data = result_response.json()

            self.last_sync_timestamp = int(time.time() * 1000)
            self._save_session()

            logger.info("Incremental sync completed successfully")
            return tasks_data

        except requests.RequestException as e:
            logger.error("Error in incremental sync: %s", e)
            return None

    def get_tasks_full(self, include_completed: bool = False) -> dict[str, Any] | None:
        """
        Fetch all tasks from Any.do using full sync.

        Downloads all tasks regardless of when they were last updated.
        """
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        current_time = int(time.time() * 1000)
        if self.last_full_sync_timestamp:
            time_since_last = current_time - self.last_full_sync_timestamp
            if time_since_last < SyncConstants.FULL_SYNC_RATE_LIMIT_MS:
                seconds_left = (SyncConstants.FULL_SYNC_RATE_LIMIT_MS - time_since_last) / 1000
                logger.warning("Full sync rate limited. Wait %.1f seconds.", seconds_left)
                return None

        try:
            sync_url = f"{self.base_url}/api/v14/me/bg_sync"
            params = {"updatedSince": 0, "includeNonVisible": "false"}

            sync_response = self.session.get(sync_url, params=params, timeout=AuthConstants.REQUEST_TIMEOUT)
            sync_response.raise_for_status()

            task_id = sync_response.json().get("task_id")
            if not task_id:
                logger.error("Could not get sync task ID for full sync")
                return None

            result_response = self._poll_for_result(task_id, SyncConstants.MAX_POLL_WAIT_FULL_SYNC)
            if result_response is None:
                logger.warning("Full sync operation timed out")
                return None

            tasks_data = result_response.json()

            self.last_sync_timestamp = int(time.time() * 1000)
            self.last_full_sync_timestamp = int(time.time() * 1000)
            self._save_session()

            logger.info("Full sync completed successfully")
            return tasks_data

        except requests.RequestException as e:
            logger.error("Error in full sync: %s", e)
            return None

    # -------------------------------------------------------------------------
    # Task operations
    # -------------------------------------------------------------------------

    def create_task(
        self,
        title: str,
        *,
        category_id: str | None = None,
        note: str = "",
        labels: list[str] | None = None,
        priority: str = "Normal",
        due_date: int = 0,
    ) -> dict[str, Any] | None:
        """
        Create a new task via PUT /me/tasks.

        Args:
            title: Task title (required).
            category_id: List/category ID. If None, uses the first available category.
            note: Optional note text.
            labels: Optional list of label IDs (e.g. the Buy tag).
            priority: "Normal", "High", or "Low".
            due_date: Unix timestamp in ms, or 0 for no due date.

        Returns:
            The created task dict from the API, or None on failure.
        """
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        now = int(time.time() * 1000)
        task_id = uuid.uuid4().hex[:24]

        task_payload = {
            "id": task_id,
            "globalTaskId": task_id,
            "title": title,
            "status": "UNCHECKED",
            "categoryId": category_id or "",
            "priority": priority,
            "creationDate": now,
            "lastUpdateDate": now,
            "dueDate": due_date,
            "dueDateUpdateTime": now,
            "titleUpdateTime": now,
            "statusUpdateTime": now,
            "categoryIdUpdateTime": now,
            "noteUpdateTime": now,
            "priorityUpdateTime": now,
            "positionUpdateTime": now,
            "repeatingMethod": "TASK_REPEAT_OFF",
            "shared": False,
            "note": note,
            "parentGlobalTaskId": None,
            "subTasks": [],
            "participants": [],
        }
        if labels:
            task_payload["labels"] = labels
            task_payload["labelsUpdateTime"] = now

        try:
            url = f"{self.base_url}/me/tasks"
            response = self.session.put(url, json=[task_payload], timeout=AuthConstants.REQUEST_TIMEOUT)

            if response.status_code == 200:
                created = response.json()
                if isinstance(created, list) and created:
                    logger.info("Created task: %s (%s)", title, created[0].get("id"))
                    return created[0]
                logger.info("Created task: %s", title)
                return task_payload

            logger.warning("Failed to create task: HTTP %d", response.status_code)
            return None

        except requests.RequestException as e:
            logger.error("Error creating task: %s", e)
            return None

    def delete_task(self, task_id: str) -> bool:
        """Delete a task by its ID. Returns True if the task was deleted (HTTP 204)."""
        if not self.logged_in:
            logger.warning("Not logged in")
            return False

        try:
            url = f"{self.base_url}/me/tasks/{task_id}"
            response = self.session.delete(url, timeout=AuthConstants.REQUEST_TIMEOUT)

            if response.status_code == 204:
                logger.info("Deleted task %s", task_id)
                return True

            logger.warning("Failed to delete task %s: HTTP %d", task_id, response.status_code)
            return False

        except requests.RequestException as e:
            logger.error("Error deleting task %s: %s", task_id, e)
            return False

    def get_label_id(self, label_name: str, tasks_data: dict[str, Any] | None = None) -> str | None:
        """Look up a label/tag ID by its display name (case-insensitive)."""
        if tasks_data is None:
            tasks_data = self.get_tasks()
        if not tasks_data:
            return None

        labels = tasks_data.get("models", {}).get("label", {}).get("items", [])
        for label in labels:
            if label.get("name", "").lower() == label_name.lower() and not label.get("isDeleted"):
                return label["id"]
        return None

    def get_category_id(self, category_name: str, tasks_data: dict[str, Any] | None = None) -> str | None:
        """Look up a category/list ID by its display name (case-insensitive)."""
        if tasks_data is None:
            tasks_data = self.get_tasks()
        if not tasks_data:
            return None

        categories = tasks_data.get("models", {}).get("category", {}).get("items", [])
        for cat in categories:
            if cat.get("name", "").lower() == category_name.lower() and not cat.get("isDeleted"):
                return cat["id"]
        return None

    # -------------------------------------------------------------------------
    # Change detection
    # -------------------------------------------------------------------------

    def _calculate_data_hash(self, data: dict[str, Any]) -> str:
        """Calculate hash of task data for change detection."""
        data_str = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data_str.encode()).hexdigest()

    def _has_meaningful_task_data(self, tasks_data: dict[str, Any]) -> bool:
        """Check if tasks_data contains meaningful task information worth saving."""
        if not tasks_data:
            return False

        if "models" in tasks_data:
            models = tasks_data["models"]

            if "task" in models and models["task"].get("items"):
                return True

            if "category" in models and models["category"].get("items"):
                categories = models["category"]["items"]
                if any(cat.get("name", "").strip() for cat in categories):
                    return True

            meaningful_models = [
                "user",
                "label",
                "attachment",
                "sharedMember",
                "space",
                "board",
                "section",
                "customField",
                "tag",
                "card",
            ]
            for model_name in meaningful_models:
                if model_name in models and models[model_name].get("items"):
                    return True

        elif "tasks" in tasks_data and tasks_data["tasks"] or "categories" in tasks_data and tasks_data["categories"]:
            return True

        return False

    # -------------------------------------------------------------------------
    # Export
    # -------------------------------------------------------------------------

    def save_tasks_to_file(self, tasks_data: dict[str, Any]) -> str | None:
        """Save tasks to timestamped JSON file with change detection."""
        if not tasks_data:
            logger.warning("No tasks data to save")
            return None

        if not self._has_meaningful_task_data(tasks_data):
            logger.info("No meaningful task data to save - skipping file creation")
            return None

        current_hash = self._calculate_data_hash(tasks_data)

        if self.last_data_hash == current_hash:
            logger.info("No changes detected since last export - skipping file creation")
            return None

        os.makedirs("outputs/raw-json", exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M-%S")
        filename = f"{timestamp}_anydo-tasks.json"
        filepath = os.path.join("outputs/raw-json", filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(tasks_data, f, indent=2, ensure_ascii=False)

            self.last_data_hash = current_hash

            file_size = os.path.getsize(filepath)
            size_mb = file_size / (1024 * 1024)

            logger.info("Tasks exported to: %s (%.2f MB)", filepath, size_mb)

            self._save_markdown_from_json(tasks_data, timestamp)

            return filepath

        except OSError as e:
            logger.error("Error saving tasks: %s", e)
            return None

    def _save_markdown_from_json(self, tasks_data: dict[str, Any], timestamp: str) -> str | None:
        """
        Generate markdown file directly from JSON data.
        Only creates new file if the human-useful data has changed.
        """
        try:
            pretty_data = self._extract_pretty_data(tasks_data, verbose=False)

            current_pretty_hash = self._calculate_data_hash(pretty_data)

            if self.last_pretty_hash == current_pretty_hash:
                logger.info("No changes in human-readable data - skipping markdown generation")
                return None

            markdown_file = self._save_markdown_tasks(pretty_data, timestamp, verbose=False)

            self.last_pretty_hash = current_pretty_hash

            return markdown_file

        except (KeyError, TypeError) as e:
            logger.error("Error saving markdown from JSON: %s", e)
            return None

    def _save_markdown_tasks(self, pretty_data: dict[str, Any], timestamp: str, verbose: bool = False) -> str | None:
        """Generate markdown table from pretty task data."""
        try:
            os.makedirs("outputs/markdown", exist_ok=True)

            suffix = "-verbose" if verbose else ""
            filename = f"{timestamp}_anydo-tasks{suffix}.md"
            filepath = os.path.join("outputs/markdown", filename)

            markdown_content = self._generate_markdown_content(pretty_data, verbose)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(markdown_content)

            file_size = os.path.getsize(filepath)
            size_kb = file_size / 1024

            mode_text = "verbose " if verbose else ""
            logger.info("Markdown %stable exported to: %s (%.1f KB)", mode_text, filepath, size_kb)

            return filepath

        except OSError as e:
            logger.error("Error saving markdown tasks: %s", e)
            return None

    # -------------------------------------------------------------------------
    # Markdown generation
    # -------------------------------------------------------------------------

    def _generate_markdown_content(self, pretty_data: dict[str, Any], verbose: bool = False) -> str:
        """Generate markdown content from pretty task data."""
        lines = []

        mode = "Verbose" if verbose else "Clean"
        lines.append(f"# 📋 Any.do Tasks Export ({mode} Mode)")
        lines.append("")
        lines.append(f"*Generated: {pretty_data.get('export_info', {}).get('extracted_at', 'Unknown')}*")
        lines.append("")

        export_info = pretty_data.get("export_info", {})
        lines.append("## 📊 Export Summary")
        lines.append("")
        lines.append("| Metric | Count |")
        lines.append("|--------|-------|")
        lines.append(f"| 📋 Total Tasks | {export_info.get('total_tasks', 0)} |")
        lines.append(f"| ⏳ Pending Tasks | {export_info.get('pending_tasks', 0)} |")
        lines.append(f"| ✅ Completed Tasks | {export_info.get('completed_tasks', 0)} |")
        lines.append("")

        lists_info = pretty_data.get("lists", {})
        if lists_info:
            lines.append("## 📁 Lists Summary")
            lines.append("")
            lines.append("| List Name | Total | ⏳ Pending | ✅ Completed |")
            lines.append("|-----------|-------|---------|-----------|")

            for list_name, list_data in lists_info.items():
                total = list_data.get("task_count", 0)
                pending = list_data.get("pending_count", 0)
                completed = list_data.get("completed_count", 0)
                lines.append(f"| {list_name} | {total} | {pending} | {completed} |")
            lines.append("")

        tasks_data = pretty_data.get("tasks", {})
        if tasks_data:
            lines.append("## 📝 Tasks")
            lines.append("")

            all_tasks = []
            for list_name, tasks in tasks_data.items():
                for task in tasks:
                    task_with_list = task.copy()
                    task_with_list["list_name"] = list_name
                    all_tasks.append(task_with_list)

            sorted_tasks = self._sort_tasks_for_display(all_tasks)

            if verbose:
                lines.append("| Title | List | Created | Due | Priority | Assignee |")
                lines.append("|-------|------|----------------------|---------------------|----------|----------|")
            else:
                lines.append("| Title | List | Created | Due |")
                lines.append("|-------|------|----------------------|---------------------|")

            for task in sorted_tasks:
                status_emoji = self._get_status_emoji(task, verbose)
                title = self._format_task_title(task)
                list_name = task.get("list_name", "Unknown")

                created_full = task.get("created_date", "N/A")
                created = created_full.split(" ")[0] if created_full != "N/A" and " " in created_full else created_full

                due = task.get("due_date", "")

                title_cell = f"{status_emoji}{title}" if status_emoji else title

                note = task.get("note")
                if note and note.strip():
                    wrapped_note = self._wrap_text(note.strip(), markdown_safe=True)
                    note_formatted = wrapped_note.replace("<br>", "<br>&nbsp;&nbsp;&nbsp;")
                    title_cell += f' <br> &nbsp;&nbsp;&nbsp;<span style="color: #666; font-style: italic;">{note_formatted}</span>'

                subtasks = task.get("subtasks", [])
                if subtasks:
                    subtask_lines = []
                    for subtask in subtasks:
                        subtask_status = self._get_status_emoji(subtask, verbose)
                        subtask_title = self._wrap_text(
                            subtask.get("title", "Untitled"), markdown_safe=True, truncate_long_lines=False
                        )
                        if subtask_status:
                            subtask_lines.append(f"&nbsp;&nbsp;&nbsp;√&nbsp;&nbsp;{subtask_title}")
                        else:
                            subtask_lines.append(f"&nbsp;&nbsp;&nbsp;- {subtask_title}")

                    subtask_content = "<br>".join(subtask_lines)
                    title_cell += f"<br>{subtask_content}"

                if verbose:
                    priority = task.get("priority", "normal")
                    priority_emoji = self._get_priority_emoji(priority)
                    assignee = task.get("assignee", "")
                    assignee_display = f"👤 {assignee}" if assignee else ""

                    lines.append(
                        f"| {title_cell} | {list_name} | 📅 {created} | {due} | {priority_emoji} {priority} | {assignee_display} |"
                    )
                else:
                    due_display = f"⏰ {due}" if due else ""
                    lines.append(f"| {title_cell} | {list_name} | 📅 {created} | {due_display} |")

            lines.append("")

        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # Data extraction
    # -------------------------------------------------------------------------

    def _extract_pretty_data(self, tasks_data: dict[str, Any], verbose: bool = False) -> dict[str, Any]:
        """Extract human-readable task information from raw API data."""
        try:
            export_info: ExportInfo = {
                "extracted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_tasks": 0,
                "pending_tasks": 0,
                "completed_tasks": 0,
            }

            # Build category lookup dict once
            category_lookup: dict[str, dict[str, Any]] = {}
            if "models" in tasks_data and "category" in tasks_data["models"]:
                for cat in tasks_data["models"]["category"]["items"]:
                    category_lookup[cat.get("id", "")] = cat

            lists_info: dict[str, ListInfo] = {}
            for cat in category_lookup.values():
                list_name = cat.get("name", "Unknown List")
                list_info: ListInfo = {"task_count": 0, "pending_count": 0, "completed_count": 0}

                if verbose:
                    list_info["color"] = cat.get("color")
                    list_info["is_default"] = cat.get("isDefault", False)

                lists_info[list_name] = list_info

            all_tasks: list[TaskInfo] = []
            tasks_by_id: dict[str, TaskInfo] = {}

            include_seconds = verbose

            if "models" in tasks_data and "task" in tasks_data["models"]:
                for task in tasks_data["models"]["task"]["items"]:
                    task_id = task.get("globalTaskId")
                    parent_id = task.get("parentGlobalTaskId")

                    task_info: TaskInfo = {
                        "title": task.get("title", "Untitled Task"),
                        "id": task_id,
                        "parent_id": parent_id,
                    }

                    if task.get("creationDate"):
                        task_info["created_date"] = self._format_timestamp(
                            task["creationDate"], include_seconds=include_seconds
                        )

                    if task.get("lastUpdateDate"):
                        task_info["last_update"] = self._format_timestamp(
                            task["lastUpdateDate"], include_seconds=include_seconds
                        )

                    if task.get("dueDate"):
                        task_info["due_date"] = self._format_timestamp(task["dueDate"], include_seconds=include_seconds)

                    list_name = "Unknown List"
                    cat_id = task.get("categoryId")
                    if cat_id and cat_id in category_lookup:
                        list_name = category_lookup[cat_id].get("name", "Unknown List")
                    task_info["list_name"] = list_name

                    note = task.get("note")
                    if note and note.strip():
                        task_info["note"] = note.strip()

                    if task.get("labels"):
                        task_info["tags"] = task["labels"]

                    is_completed = task.get("status") == "CHECKED"
                    task_info["_internal_status"] = "completed" if is_completed else "pending"

                    if verbose:
                        task_info["status"] = "completed" if is_completed else "pending"
                        task_info["priority"] = task.get("priority", "Normal").lower()
                        task_info["assignee"] = task.get("assignedTo")
                        task_info["repeating"] = task.get("repeatingMethod", "TASK_REPEAT_OFF")

                        if cat_id and cat_id in category_lookup:
                            task_info["list_color"] = category_lookup[cat_id].get("color")
                        else:
                            task_info["list_color"] = None

                    export_info["total_tasks"] += 1
                    if is_completed:
                        export_info["completed_tasks"] += 1
                    else:
                        export_info["pending_tasks"] += 1

                    if list_name in lists_info:
                        lists_info[list_name]["task_count"] += 1
                        if is_completed:
                            lists_info[list_name]["completed_count"] += 1
                        else:
                            lists_info[list_name]["pending_count"] += 1

                    tasks_by_id[task_id] = task_info
                    all_tasks.append(task_info)

            parent_tasks: list[TaskInfo] = []
            subtasks_by_parent: dict[str, list[TaskInfo]] = {}

            for task in all_tasks:
                if task.get("parent_id") is None:
                    parent_tasks.append(task)
                else:
                    parent_id = task["parent_id"]
                    if parent_id not in subtasks_by_parent:
                        subtasks_by_parent[parent_id] = []
                    subtasks_by_parent[parent_id].append(task)

            for parent_task in parent_tasks:
                parent_id = parent_task.get("id")
                if parent_id and parent_id in subtasks_by_parent:
                    subtasks = sorted(subtasks_by_parent[parent_id], key=lambda x: x.get("title", ""))

                    for subtask in subtasks:
                        subtask.pop("id", None)
                        subtask.pop("parent_id", None)
                    parent_task["subtasks"] = subtasks

            for task in parent_tasks:
                task.pop("id", None)
                task.pop("parent_id", None)

            tasks_by_list: dict[str, list[TaskInfo]] = {}
            for task in parent_tasks:
                list_name = task.get("list_name", "Unknown List")
                if list_name not in tasks_by_list:
                    tasks_by_list[list_name] = []
                tasks_by_list[list_name].append(task)

            for list_name in tasks_by_list:
                tasks_by_list[list_name].sort(key=lambda x: x.get("title", ""))

            return {"export_info": export_info, "lists": lists_info, "tasks": tasks_by_list}

        except (KeyError, TypeError) as e:
            logger.warning("Error extracting pretty data: %s", e)
            return {"export_info": {"error": str(e)}, "lists": {}, "tasks": {}}

    # -------------------------------------------------------------------------
    # Display helpers
    # -------------------------------------------------------------------------

    def get_simple_tasks(self, tasks_data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Get a simplified list of tasks with just the essential information."""
        if tasks_data is None:
            tasks_data = self.get_tasks()
        if not tasks_data:
            return []

        simple_tasks: list[dict[str, Any]] = []

        if "models" in tasks_data and "task" in tasks_data["models"]:
            task_items = tasks_data["models"]["task"].get("items", [])
            for task in task_items:
                simple_tasks.append(
                    {
                        "title": task.get("title", "Untitled"),
                        "completed": task.get("status") == "CHECKED",
                        "due_date": task.get("dueDate"),
                        "priority": task.get("priority", "NORMAL"),
                        "list_id": task.get("categoryId"),
                        "id": task.get("id"),
                        "note": task.get("note"),
                        "creation_date": task.get("creationDate"),
                        "last_update": task.get("lastUpdateDate"),
                    }
                )

        elif "tasks" in tasks_data:
            for task in tasks_data["tasks"]:
                simple_tasks.append(
                    {
                        "title": task.get("title", "Untitled"),
                        "completed": task.get("status") == "DONE",
                        "due_date": task.get("dueDate"),
                        "priority": task.get("priority", "NORMAL"),
                        "list_id": task.get("categoryId"),
                        "id": task.get("id"),
                    }
                )

        return simple_tasks

    def get_lists(self, tasks_data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Get all task lists/categories."""
        if tasks_data is None:
            tasks_data = self.get_tasks()
        if not tasks_data:
            return []

        lists: list[dict[str, Any]] = []

        if "models" in tasks_data and "category" in tasks_data["models"]:
            category_items = tasks_data["models"]["category"].get("items", [])
            for category in category_items:
                list_info = {
                    "id": category.get("id"),
                    "name": category.get("name", "Untitled List"),
                    "color": category.get("color"),
                    "is_default": category.get("isDefault", False),
                    "position": category.get("position"),
                    "is_deleted": category.get("isDeleted", False),
                }
                if not list_info["is_deleted"]:
                    lists.append(list_info)

        elif "categories" in tasks_data:
            for category in tasks_data["categories"]:
                lists.append(
                    {
                        "id": category.get("id"),
                        "name": category.get("name", "Untitled List"),
                        "color": category.get("color"),
                        "is_default": category.get("isDefault", False),
                    }
                )

        return lists

    def print_tasks_summary(self, tasks_data: dict[str, Any] | None = None) -> None:
        """Print a nice summary of all tasks."""
        tasks = self.get_simple_tasks(tasks_data)
        lists = self.get_lists(tasks_data)

        if not tasks:
            logger.info("No tasks found")
            return

        list_names = {lst["id"]: lst["name"] for lst in lists}

        pending_tasks = [t for t in tasks if not t["completed"]]
        completed_tasks = [t for t in tasks if t["completed"]]

        logger.info("Found %d tasks (%d pending, %d completed)", len(tasks), len(pending_tasks), len(completed_tasks))

        for task in pending_tasks:
            list_name = list_names.get(task["list_id"], "Unknown List")
            due_info = f" (Due: {task['due_date']})" if task["due_date"] else ""
            logger.info("  [%s] %s%s", list_name, task["title"], due_info)

    # -------------------------------------------------------------------------
    # Text formatting utilities
    # -------------------------------------------------------------------------

    def _sort_tasks_for_display(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Sort tasks for display: pending with due dates first (by due date),
        then pending without due dates (newest first), then completed (newest first).
        """

        def parse_date(date_str: str | None) -> datetime | None:
            if not date_str:
                return None
            try:
                if " " in date_str:
                    return datetime.strptime(date_str, "%Y-%m-%d %H:%M")
                return datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                return None

        def sort_key(task: dict[str, Any]) -> tuple:
            internal_status = task.get("_internal_status", "pending")
            is_completed = internal_status == "completed"

            created_date = parse_date(task.get("created_date", ""))
            due_date = parse_date(task.get("due_date", ""))

            created_timestamp = created_date.timestamp() if created_date else 0

            if is_completed:
                return (1, -created_timestamp)
            if due_date:
                return (0, due_date.timestamp(), -created_timestamp)
            return (0, float("inf"), -created_timestamp)

        return sorted(tasks, key=sort_key)

    def _get_status_emoji(self, task: dict[str, Any], verbose: bool = False) -> str:
        """Get status emoji for a task."""
        if verbose:
            status = task.get("status", "pending")
            return "√&nbsp;&nbsp;" if status == "completed" else ""
        internal_status = task.get("_internal_status")
        if internal_status:
            return "√&nbsp;&nbsp;" if internal_status == "completed" else ""
        return ""

    def _get_priority_emoji(self, priority: str) -> str:
        """Get priority emoji."""
        priority_lower = priority.lower()
        if priority_lower == "high":
            return "🔴"
        if priority_lower == "medium":
            return "🟡"
        return "🟢"

    def _format_task_title(self, task: dict[str, Any]) -> str:
        """Format task title with markdown-safe text truncation."""
        title = task.get("title", "Untitled Task")
        return self._wrap_text(title, markdown_safe=True, truncate_long_lines=True)

    def _format_timestamp(self, timestamp: int, include_seconds: bool = True) -> str:
        """Format a timestamp (unix ms) to a human-readable string."""
        try:
            timestamp_seconds = int(timestamp) / 1000
            dt = datetime.fromtimestamp(timestamp_seconds)

            if include_seconds:
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError, OSError):
            return "Invalid date"

    def _wrap_text(
        self, text: str, width: int | None = None, markdown_safe: bool = False, truncate_long_lines: bool = False
    ) -> str:
        """Wrap text to specified width, preserving line breaks."""
        if not text:
            return text

        wrap_width = width or (100 if markdown_safe else self.text_wrap_width)
        lines = text.split("\n")
        separator = "<br>" if markdown_safe else "\n"

        if markdown_safe and truncate_long_lines:
            processed = []
            for line in lines:
                if len(line) <= wrap_width:
                    processed.append(line)
                else:
                    processed.append(line[: wrap_width - 3] + "...")
            return separator.join(processed)

        all_wrapped = []
        for line in lines:
            if len(line) <= wrap_width:
                all_wrapped.append(line)
            else:
                all_wrapped.extend(
                    textwrap.wrap(line, width=wrap_width, break_long_words=False, break_on_hyphens=False)
                )

        return separator.join(all_wrapped)
