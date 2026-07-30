"""
Any.do API Client

A Python client for the Any.do API with session persistence, 2FA support,
and efficient sync strategies.
"""

import hashlib
import json
import logging
import mimetypes
import os
import sys
import textwrap
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

__all__ = [
    "AnyDoClient",
    "TaskInfo",
    "ListInfo",
    "ExportInfo",
    "TagInfo",
    "AttachmentInfo",
    "AgentTaskInfo",
    "AgentExportInfo",
    "send_ntfy",
]


def _ntfy_state_path(ntfy_config: dict[str, Any]) -> Path:
    custom = ntfy_config.get("state_file")
    if custom:
        return Path(custom)
    session_file = os.environ.get("ANYDO_SESSION_FILE", "session.json")
    return Path(session_file).parent / ".ntfy-state.json"


def _load_ntfy_state(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(key): float(value) for key, value in data.items()}
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("Could not read ntfy state file %s: %s", path, exc)
    return {}


def _save_ntfy_state(path: Path, state: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _is_ntfy_rate_limited(ntfy_config: dict[str, Any], rate_limit_key: str) -> bool:
    interval = int(ntfy_config.get("rate_limit_seconds", 0) or 0)
    if interval <= 0:
        return False
    state = _load_ntfy_state(_ntfy_state_path(ntfy_config))
    last_sent = state.get(rate_limit_key, 0.0)
    return (time.time() - last_sent) < interval


def _record_ntfy_sent(ntfy_config: dict[str, Any], rate_limit_key: str) -> None:
    state_path = _ntfy_state_path(ntfy_config)
    state = _load_ntfy_state(state_path)
    state[rate_limit_key] = time.time()
    _save_ntfy_state(state_path, state)


def send_ntfy(
    ntfy_config: dict[str, Any] | None,
    title: str,
    message: str,
    priority: int | None = None,
    tags: list[str] | None = None,
    *,
    rate_limit_key: str | None = None,
) -> bool:
    """
    Send a notification via ntfy.sh.

    Args:
        ntfy_config: Configuration dict with 'enabled', 'url', 'topic', 'token' keys
        title: Notification title
        message: Notification message body
        priority: 1-5, where 5 is highest (defaults to ntfy_config['priority'] or 3)
        tags: Optional list of emoji tags
        rate_limit_key: Optional key for per-alert rate limiting (see rate_limit_seconds)

    Returns:
        True if sent successfully, False otherwise or if ntfy is not configured/enabled
    """
    if not ntfy_config or not ntfy_config.get("enabled"):
        return False

    if rate_limit_key and _is_ntfy_rate_limited(ntfy_config, rate_limit_key):
        logger.debug("ntfy notification rate limited for key %s", rate_limit_key)
        return False

    resolved_priority = priority if priority is not None else int(ntfy_config.get("priority", 3))

    try:
        url = ntfy_config.get("url", "https://ntfy.sh")
        topic = ntfy_config.get("topic", "anydo-alerts")
        token = ntfy_config.get("token")
        notification_url = f"{url}/{topic}"

        headers = {
            "Title": title,
            "Priority": str(max(1, min(5, resolved_priority))),
        }

        if tags:
            headers["Tags"] = ",".join(tags)

        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = requests.post(notification_url, data=message, headers=headers, timeout=10)
        if response.status_code == 200:
            if rate_limit_key:
                _record_ntfy_sent(ntfy_config, rate_limit_key)
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


class TagInfo(TypedDict, total=False):
    """Type definition for tag/label information."""

    id: str
    name: str
    color: str
    is_deleted: bool
    is_predefined: bool


class AttachmentInfo(TypedDict, total=False):
    """Type definition for attachment information."""

    id: str
    global_task_id: str
    display_name: str
    mime_type: str
    file_size: int
    url: str
    deleted: bool
    creation_date: int
    last_update_date: int


class AgentTaskInfo(TypedDict, total=False):
    """Compact task record for agent exports."""

    id: str
    title: str
    list_id: str
    list: str
    tag_ids: list[str]
    tags: list[str]
    due_ms: int
    creation_ms: int
    note: str
    subtasks: list["AgentTaskInfo"]


class AgentExportInfo(TypedDict, total=False):
    """Token-efficient export for agents."""

    exported_at: str
    pending_tasks: int
    lists: list[dict[str, str]]
    tags: list[dict[str, str]]
    tasks: list[AgentTaskInfo]


_TASK_MUTATION_FIELDS: dict[str, tuple[str, str]] = {
    "title": ("title", "titleUpdateTime"),
    "note": ("note", "noteUpdateTime"),
    "status": ("status", "statusUpdateTime"),
    "category_id": ("categoryId", "categoryIdUpdateTime"),
    "due_date": ("dueDate", "dueDateUpdateTime"),
    "labels": ("labels", "labelsUpdateTime"),
    "priority": ("priority", "priorityUpdateTime"),
    "alert": ("alert", "alertUpdateTime"),
    "parent_global_task_id": ("parentGlobalTaskId", "parentGlobalTaskIdUpdateTime"),
}

# Fields the native/web clients push via POST /api/v14/me/sync (not reliable through
# bare PUT /me/tasks partial updates on existing rows). Reorder/position is also sync-engine-only.
# creationDate is intentionally excluded — server treats it as immutable after create.
_SYNC_ENGINE_MUTATION_FIELDS = frozenset(_TASK_MUTATION_FIELDS.keys())

_SYNC_MODEL_NAMES = (
    "attachment",
    "category",
    "label",
    "task",
    "space",
    "board",
    "section",
    "userCustomView",
    "customField",
    "customFieldValue",
    "card",
    "tag",
    "myDayEntry",
    "user",
    "groceryBoard",
    "grocerySection",
    "groceryCard",
    "cardChecklist",
    "checklistItem",
    "cardAttachment",
)


def _empty_sync_models() -> dict[str, dict[str, list[Any]]]:
    return {name: {"items": []} for name in _SYNC_MODEL_NAMES}


def _task_record_to_sync_dto(task: dict[str, Any]) -> dict[str, Any]:
    """Map a pulled task record to the web client's sync push DTO shape."""
    return {
        "id": task.get("globalTaskId") or task.get("id"),
        "globalTaskId": task.get("globalTaskId") or task.get("id"),
        "alert": task.get("alert"),
        "appleReminderId": task.get("appleReminderId"),
        "categoryId": task.get("categoryId"),
        "chatConversationId": task.get("chatConversationId"),
        "creationDate": task.get("creationDate"),
        "dueDate": task.get("dueDate"),
        "labels": task.get("labels") or [],
        "note": task.get("note") or "",
        "parentGlobalTaskId": task.get("parentGlobalTaskId"),
        "position": task.get("position"),
        "priority": task.get("priority"),
        "repeatingMethod": task.get("repeatingMethod", "TASK_REPEAT_OFF"),
        "status": task.get("status"),
        "title": task.get("title"),
        "alertUpdateTime": task.get("alertUpdateTime"),
        "categoryIdUpdateTime": task.get("categoryIdUpdateTime"),
        "chatConversationIdUpdateTime": task.get("chatConversationIdUpdateTime"),
        "dueDateUpdateTime": task.get("dueDateUpdateTime"),
        "labelsUpdateTime": task.get("labelsUpdateTime"),
        "lastUpdateDate": task.get("lastUpdateDate"),
        "noteUpdateTime": task.get("noteUpdateTime"),
        "positionUpdateTime": task.get("positionUpdateTime"),
        "priorityUpdateTime": task.get("priorityUpdateTime"),
        "statusUpdateTime": task.get("statusUpdateTime"),
        "titleUpdateTime": task.get("titleUpdateTime"),
        "evernoteNotes": task.get("evernoteNotes"),
        "latitude": task.get("latitude"),
        "longitude": task.get("longitude"),
        "participants": task.get("participants") or [],
        "subTasks": task.get("subTasks") or [],
    }


def _payload_mutation_values(payload: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field_name, (api_field, _update_time_field) in _TASK_MUTATION_FIELDS.items():
        if api_field in payload:
            values[field_name] = payload[api_field]
    return values


def _normalize_mutation_value(field_name: str, value: Any) -> Any:
    """Normalize values for comparison — sync echo and GET /me/tasks/{id} differ on null vs 0."""
    if field_name == "due_date":
        if value in (None, 0, ""):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if field_name == "labels":
        return tuple(sorted(value or []))
    if field_name == "alert":
        if not isinstance(value, dict):
            return value
        return (
            value.get("type"),
            value.get("offset"),
            value.get("repeatEndType"),
        )
    return value


def _response_matches_mutation(expected: dict[str, Any], task_record: dict[str, Any]) -> bool:
    for field_name, expected_value in expected.items():
        api_field, _ = _TASK_MUTATION_FIELDS[field_name]
        actual = task_record.get(api_field)
        if _normalize_mutation_value(field_name, actual) != _normalize_mutation_value(
            field_name, expected_value
        ):
            return False
    return True


def _payloads_with_echo_mismatch(
    payloads: list[dict[str, Any]],
    echoed_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return payloads whose mutation was not confirmed by a sync/PUT response echo."""
    mismatched: list[dict[str, Any]] = []
    for payload in payloads:
        task_id = payload.get("globalTaskId") or payload.get("id")
        expected = _payload_mutation_values(payload)
        if not expected or not task_id:
            continue
        echoed = echoed_by_id.get(task_id)
        if echoed is None or not _response_matches_mutation(expected, echoed):
            mismatched.append(payload)
    return mismatched


def _merge_expected_mutations(payloads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge expected field values per task id (for one refetch covering a batch)."""
    merged: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        task_id = payload.get("globalTaskId") or payload.get("id")
        expected = _payload_mutation_values(payload)
        if not task_id or not expected:
            continue
        merged.setdefault(task_id, {}).update(expected)
    return merged


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
        self.last_mutation_timestamp: int | None = None
        self.client_id = str(uuid.uuid4())
        self.rotate_client_id = rotate_client_id
        self.auth_token: str | None = None
        self.server_last_update_date: int | None = None
        self.client_sync_counter: int = 0

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
            self.last_mutation_timestamp = session_data.get("last_mutation_timestamp")
            if session_data.get("client_id") and not self.rotate_client_id:
                self.client_id = session_data["client_id"]

            self.auth_token = session_data.get("auth_token")
            if self.auth_token:
                self.session.headers["X-Anydo-Auth"] = self.auth_token
            self.server_last_update_date = session_data.get("server_last_update_date")
            self.client_sync_counter = int(session_data.get("client_sync_counter") or 0)

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
                "last_mutation_timestamp": self.last_mutation_timestamp,
                "auth_token": self.auth_token,
                "server_last_update_date": self.server_last_update_date,
                "client_sync_counter": self.client_sync_counter,
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
                    self._save_session()
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
                    self._save_session()
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
            if response.status_code in (202, 404):
                # 202 = still processing; 404 = result not registered yet
                poll_interval = min(
                    poll_interval * SyncConstants.POLL_BACKOFF_MULTIPLIER, SyncConstants.MAX_POLL_INTERVAL
                )
                continue
            response.raise_for_status()

        return None

    def _note_mutation(self) -> None:
        """Record a REST/sync-push write that bg_sync incremental may not reflect yet."""
        self.last_mutation_timestamp = int(time.time() * 1000)
        self._save_session()

    def _sync_is_stale(self) -> bool:
        """True when local writes happened after the last successful sync pull."""
        if self.last_mutation_timestamp is None:
            return False
        if self.last_sync_timestamp is None:
            return True
        return self.last_mutation_timestamp > self.last_sync_timestamp

    @staticmethod
    def export_sync_stale(export: dict[str, Any]) -> bool:
        """True when an agent export was written before pending local mutations synced."""
        mutation = export.get("last_mutation_timestamp")
        sync = export.get("last_sync_timestamp")
        if mutation is None:
            return False
        if sync is None:
            return True
        return mutation > sync

    def _commit_sync_timestamps(self, *, full_sync: bool = False) -> None:
        """Persist sync cursors after a successful end-to-end sync."""
        self.last_sync_timestamp = int(time.time() * 1000)
        if full_sync:
            self.last_full_sync_timestamp = self.last_sync_timestamp
        if (
            self.last_mutation_timestamp is not None
            and self.last_sync_timestamp >= self.last_mutation_timestamp
        ):
            self.last_mutation_timestamp = None
        self._save_session()

    def _capture_sync_response(self, tasks_data: dict[str, Any] | None) -> None:
        """Persist server sync cursor from a bg_sync pull response."""
        if not tasks_data:
            return
        last_update = tasks_data.get("lastUpdateDate")
        if isinstance(last_update, int) and last_update > 0:
            self.server_last_update_date = last_update
            self._save_session()

    def _next_client_sync_id(self) -> int:
        self.client_sync_counter += 1
        return self.client_sync_counter

    def _apply_mutation_payload_to_sync_dto(
        self, dto: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        merged = dict(dto)
        now = int(payload.get("lastUpdateDate") or time.time() * 1000)
        merged["lastUpdateDate"] = now
        for field_name, (api_field, update_time_field) in _TASK_MUTATION_FIELDS.items():
            if api_field in payload:
                merged[api_field] = payload[api_field]
                merged[update_time_field] = payload.get(update_time_field, now)
        return merged

    def _push_sync_tasks(self, dtos: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Push task mutations via POST /api/v14/me/sync (web/native sync engine path)."""
        if not self.logged_in or not dtos:
            return None
        if self.server_last_update_date is None:
            logger.debug("Skipping sync push: no server_last_update_date cursor yet")
            return None

        sync_id = self._next_client_sync_id()
        models = _empty_sync_models()
        models["task"] = {"items": dtos}
        body = {"syncId": sync_id, "models": models}
        params = {
            "updatedSince": self.server_last_update_date,
            "includeNonVisible": "true",
        }
        url = f"{self.base_url}/api/v14/me/sync"

        try:
            response = self.session.post(
                url, params=params, json=body, timeout=AuthConstants.REQUEST_TIMEOUT
            )
            if response.status_code != 200:
                logger.warning("Sync push failed: HTTP %d", response.status_code)
                return None
            data = response.json()
            if isinstance(data.get("lastUpdateDate"), int):
                self.server_last_update_date = data["lastUpdateDate"]
            resp_sync_id = data.get("syncId")
            if isinstance(resp_sync_id, int):
                self.client_sync_counter = max(self.client_sync_counter, resp_sync_id)
            self._save_session()
            return data
        except requests.RequestException as exc:
            logger.warning("Sync push error: %s", exc)
            return None

    def _task_record_for_sync_push(
        self,
        task_id: str,
        tasks_data: dict[str, Any] | None,
        cache: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Resolve one task row for sync push without incremental/full sync pulls."""
        if task_id in cache:
            return cache[task_id]
        if tasks_data:
            for item in tasks_data.get("models", {}).get("task", {}).get("items", []):
                if (item.get("globalTaskId") or item.get("id")) == task_id:
                    cache[task_id] = item
                    return item
        record = self._fetch_task_via_api(task_id)
        if record is not None:
            cache[task_id] = record
        return record

    def _verify_sync_push(
        self,
        payloads: list[dict[str, Any]],
        sync_response: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return payloads not confirmed by the sync push response echo."""
        echoed_by_id = {
            item.get("id") or item.get("globalTaskId"): item
            for item in sync_response.get("models", {}).get("task", {}).get("items", [])
        }
        mismatched = _payloads_with_echo_mismatch(payloads, echoed_by_id)
        for payload in mismatched:
            task_id = payload.get("globalTaskId") or payload.get("id")
            expected = _payload_mutation_values(payload)
            logger.debug(
                "Sync push echo inconclusive for task %s (fields: %s)",
                task_id,
                ", ".join(expected.keys()),
            )
        return mismatched

    def _verify_put_response(
        self, payloads: list[dict[str, Any]], response_items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return payloads not confirmed by the PUT /me/tasks response echo."""
        if len(response_items) < len(payloads):
            return [payload for payload in payloads if _payload_mutation_values(payload)]
        echoed_by_id = {
            item.get("id") or item.get("globalTaskId"): item for item in response_items
        }
        mismatched = _payloads_with_echo_mismatch(payloads, echoed_by_id)
        for payload in mismatched:
            task_id = payload.get("globalTaskId") or payload.get("id")
            expected = _payload_mutation_values(payload)
            logger.debug(
                "PUT /me/tasks echo inconclusive for task %s (fields: %s)",
                task_id,
                ", ".join(expected.keys()),
            )
        return mismatched

    def _verify_mutations_via_refetch(self, payloads: list[dict[str, Any]]) -> bool:
        """Confirm mutations via GET /me/tasks/{id} when sync/PUT echoes are ambiguous.

        Only callers with echo mismatches should invoke this — one ~4 KB GET per task id,
        not a full sync. Merges expected fields when a batch touched the same task twice.
        """
        expected_by_task = _merge_expected_mutations(payloads)
        if not expected_by_task:
            return False

        all_ok = True
        for task_id, expected in expected_by_task.items():
            record = self._fetch_task_via_api(task_id)
            if record is None:
                logger.warning("Refetch verification: task %s not found", task_id)
                all_ok = False
                continue
            if record.get("status") == "DELETED":
                logger.warning("Refetch verification: task %s is DELETED", task_id)
                all_ok = False
                continue
            if not _response_matches_mutation(expected, record):
                logger.warning(
                    "Refetch verification: task %s fields not persisted (%s)",
                    task_id,
                    ", ".join(expected.keys()),
                )
                all_ok = False
        return all_ok

    def _mutate_tasks(self, payloads: list[dict[str, Any]], *, tasks_data: dict[str, Any] | None = None) -> bool:
        """Apply task field updates via sync push when possible, then PUT with verification.

        Any.do has two mutation paths with different reliability on cookie-only sessions:
        - **Create** (new globalTaskId via PUT /me/tasks): title, note, due, alert, etc. persist.
        - **Update** (existing row): web/native clients use POST /api/v14/me/sync; bare PUT often
          returns 200 but echoes stale values.

        Responses are verified against the echo; if that fails we re-fetch only the affected
        task ids via GET /me/tasks/{id} (~4 KB each) — never a full/incremental sync for
        verification. Sync push source rows use the same per-task GET when tasks_data is absent.
        """
        if not self.logged_in:
            logger.warning("Not logged in")
            return False

        sync_candidates: list[dict[str, Any]] = []
        source_cache: dict[str, dict[str, Any]] = {}
        if self.server_last_update_date is not None:
            for payload in payloads:
                task_id = payload.get("globalTaskId") or payload.get("id")
                if not task_id:
                    continue
                task = self._task_record_for_sync_push(task_id, tasks_data, source_cache)
                if task is None:
                    continue
                sync_candidates.append(
                    self._apply_mutation_payload_to_sync_dto(
                        _task_record_to_sync_dto(task), payload
                    )
                )

        if sync_candidates:
            sync_response = self._push_sync_tasks(sync_candidates)
            if sync_response:
                echo_mismatches = self._verify_sync_push(payloads, sync_response)
                if not echo_mismatches:
                    logger.info("Applied task mutation via sync push")
                    self._note_mutation()
                    return True
                if self._verify_mutations_via_refetch(echo_mismatches):
                    logger.info("Applied task mutation via sync push (confirmed by refetch)")
                    self._note_mutation()
                    return True

        try:
            url = f"{self.base_url}/me/tasks"
            response = self.session.put(url, json=payloads, timeout=AuthConstants.REQUEST_TIMEOUT)
            if response.status_code != 200:
                logger.warning("Failed to update tasks: HTTP %d", response.status_code)
                return False
            body = response.json()
            if not isinstance(body, list):
                logger.warning("Unexpected PUT /me/tasks response shape")
                return False
            echo_mismatches = self._verify_put_response(payloads, body)
            if not echo_mismatches:
                self._note_mutation()
                return True
            if self._verify_mutations_via_refetch(echo_mismatches):
                logger.info("Applied task mutation via PUT (confirmed by refetch)")
                self._note_mutation()
                return True
            logger.warning(
                "Task mutation not persisted — use web UI or recreate_task "
                "(title, note, due, reminder, reorder)"
            )
            return False
        except requests.RequestException as exc:
            logger.error("Error updating tasks: %s", exc)
            return False

    def get_tasks(
        self, include_completed: bool = False, *, include_archived: bool = False
    ) -> dict[str, Any] | None:
        """
        Fetch tasks from Any.do using smart sync strategy.

        Browser-like behaviour for **backup/export**: incremental poll first, full pull only when
        the delta contains meaningful changes. When nothing changed, returns an empty/sparse
        incremental payload (often zero task rows) — that is normal, not an error.

        Do not use this as the default read path for agents (use agent export or per-task REST).
        Use get_tasks_full() when you need sync-shaped bulk data and can accept ~900 KB + 60s cooldown.
        """
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        if self._sync_is_stale():
            logger.info(
                "REST mutations since last sync — forcing full sync "
                "(incremental/export cache would miss PUT create/delete rows)"
            )
            return self.get_tasks_full(include_completed, include_archived=include_archived)

        if self.last_sync_timestamp:
            logger.info("Checking for changes with incremental sync...")
            incremental_data = self.get_tasks_incremental(
                include_completed, include_archived=include_archived, commit=False
            )

            if incremental_data is None:
                logger.warning("Incremental sync failed, falling back to full sync...")
            elif self._has_meaningful_task_data(incremental_data):
                logger.info("Changes detected, performing full sync...")
                full_data = self.get_tasks_full(include_completed, include_archived=include_archived)
                if full_data is not None:
                    return full_data
                logger.warning("Full sync failed, falling back to incremental data...")
                return incremental_data
            else:
                if self._sync_is_stale():
                    logger.info(
                        "Incremental sync empty but REST mutations pending — forcing full sync"
                    )
                    return self.get_tasks_full(
                        include_completed, include_archived=include_archived
                    )
                logger.info("No changes detected since last sync")
                self._commit_sync_timestamps(full_sync=False)
                return incremental_data

        logger.info("Performing full sync...")
        return self.get_tasks_full(include_completed, include_archived=include_archived)

    def get_tasks_incremental(
        self,
        include_completed: bool = False,
        *,
        include_archived: bool = False,
        commit: bool = True,
    ) -> dict[str, Any] | None:
        """Fetch only tasks updated since last sync."""
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        if not self.last_sync_timestamp:
            logger.warning("No last sync timestamp available")
            return None

        try:
            sync_url = f"{self.base_url}/api/v14/me/bg_sync"
            params = {
                "updatedSince": self.last_sync_timestamp,
                "includeNonVisible": self._include_non_visible(include_completed, include_archived),
            }

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
            self._capture_sync_response(tasks_data)

            if commit:
                self._commit_sync_timestamps(full_sync=False)

            logger.info("Incremental sync completed successfully")
            return tasks_data

        except requests.RequestException as e:
            logger.error("Error in incremental sync: %s", e)
            return None

    def get_tasks_full(
        self, include_completed: bool = False, *, include_archived: bool = False
    ) -> dict[str, Any] | None:
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
            params = {
                "updatedSince": 0,
                "includeNonVisible": self._include_non_visible(include_completed, include_archived),
            }

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
            self._capture_sync_response(tasks_data)

            self._commit_sync_timestamps(full_sync=True)

            logger.info("Full sync completed successfully")
            return tasks_data

        except requests.RequestException as e:
            logger.error("Error in full sync: %s", e)
            return None

    @staticmethod
    def _include_non_visible(include_completed: bool = False, include_archived: bool = False) -> str:
        """Map sync flags to Any.do includeNonVisible query parameter."""
        return "true" if (include_completed or include_archived) else "false"

    # -------------------------------------------------------------------------
    # Task operations
    # -------------------------------------------------------------------------

    def _build_new_task_payload(
        self,
        title: str,
        *,
        category_id: str | None = None,
        note: str = "",
        labels: list[str] | None = None,
        priority: str = "Normal",
        due_date: int = 0,
        alert: dict[str, Any] | None = None,
        parent_id: str | None = None,
        status: str = "UNCHECKED",
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a PUT /me/tasks payload for creating a new task (creates persist; updates often do not)."""
        now = int(time.time() * 1000)
        new_id = task_id or uuid.uuid4().hex[:24]
        payload: dict[str, Any] = {
            "id": new_id,
            "globalTaskId": new_id,
            "title": title,
            "status": status,
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
            "parentGlobalTaskId": parent_id,
            "subTasks": [],
            "participants": [],
        }
        if parent_id is not None:
            payload["parentGlobalTaskIdUpdateTime"] = now
        if labels:
            payload["labels"] = labels
            payload["labelsUpdateTime"] = now
        if alert is not None:
            payload["alert"] = alert
            payload["alertUpdateTime"] = now
        return payload

    @staticmethod
    def _apply_source_metadata(payload: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        """Copy best-effort fields from an existing task record onto a create payload.

        position and creationDate are sent on create but Any.do usually ignores them (same
        sync-engine ownership as reorder). repeatingMethod does copy when the server accepts it.
        """
        if source.get("position") is not None:
            payload["position"] = source["position"]
            if source.get("positionUpdateTime") is not None:
                payload["positionUpdateTime"] = source["positionUpdateTime"]
        if source.get("creationDate"):
            payload["creationDate"] = source["creationDate"]
        if source.get("repeatingMethod"):
            payload["repeatingMethod"] = source["repeatingMethod"]
        return payload

    @staticmethod
    def _normalize_task_status(status: str) -> str:
        """Map REST/sync status values onto the create payload convention.

        GET /me/tasks/{id} embeds completed subtasks as DONE; sync export and PUT create use CHECKED.
        """
        if status == "DONE":
            return "CHECKED"
        return status

    def _build_new_task_payload_from_record(
        self,
        source: dict[str, Any],
        *,
        title: str | None = None,
        parent_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a create payload by copying fields from an existing task row."""
        payload = self._build_new_task_payload(
            title if title is not None else (source.get("title") or ""),
            category_id=source.get("categoryId"),
            note=source.get("note") or "",
            labels=source.get("labels") or None,
            priority=source.get("priority") or "Normal",
            due_date=source.get("dueDate") or 0,
            alert=source.get("alert"),
            parent_id=parent_id,
            status=self._normalize_task_status(source.get("status") or "UNCHECKED"),
            task_id=task_id,
        )
        return self._apply_source_metadata(payload, source)

    def _register_attachment(
        self,
        task_id: str,
        *,
        display_name: str,
        mime_type: str,
        file_size: int,
        url: str,
        creation_date: int | None = None,
    ) -> bool:
        """Register an attachment row against a task (used by upload and clone)."""
        now = int(time.time() * 1000)
        attachment_payload = {
            "id": uuid.uuid4().hex[:24],
            "globalTaskId": task_id,
            "displayName": display_name,
            "mimeType": mime_type,
            "fileSize": file_size,
            "url": url,
            "deleted": False,
            "creationDate": creation_date or now,
            "lastUpdateDate": now,
        }
        try:
            register_url = f"{self.base_url}/me/attachments"
            response = self.session.put(
                register_url, json=[attachment_payload], timeout=AuthConstants.REQUEST_TIMEOUT
            )
            if response.status_code == 200:
                return True
            logger.warning("Failed to register attachment: HTTP %d", response.status_code)
            return False
        except requests.RequestException as exc:
            logger.warning("Error registering attachment: %s", exc)
            return False

    def _clone_attachments(
        self,
        source_task_id: str,
        new_task_id: str,
        tasks_data: dict[str, Any] | None,
    ) -> int:
        """Re-link attachments from source task onto a newly created task (same S3 URL).

        Files stay in S3; we only register a new attachment row on the clone via PUT /me/attachments.
        """
        cloned = 0
        for attachment in self.get_attachments(tasks_data):
            if attachment.get("global_task_id") != source_task_id:
                continue
            if self._register_attachment(
                new_task_id,
                display_name=attachment.get("display_name") or "attachment",
                mime_type=attachment.get("mime_type") or "application/octet-stream",
                file_size=int(attachment.get("file_size") or 0),
                url=attachment.get("url") or "",
                creation_date=attachment.get("creation_date"),
            ):
                cloned += 1
            else:
                logger.warning(
                    "clone_task: failed to re-link attachment %r for %s",
                    attachment.get("display_name"),
                    new_task_id,
                )
        return cloned

    def _fetch_task_via_api(self, task_id: str) -> dict[str, Any] | None:
        """Fetch one task via GET /me/tasks/{id}.

        Used by clone/recreate instead of get_tasks_full() (~900 KB). Returns ~4 KB including
        embedded subTasks. Agent export and incremental sync are unsuitable here: agent JSON is
        not sync-shaped, and incremental often has zero task rows when nothing changed.
        """
        if not self.logged_in:
            return None
        try:
            response = self.session.get(
                f"{self.base_url}/me/tasks/{task_id}",
                timeout=AuthConstants.REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                logger.warning("Failed to fetch task %s: HTTP %d", task_id, response.status_code)
                return None
            body = response.json()
            return body if isinstance(body, dict) else None
        except requests.RequestException as exc:
            logger.warning("Error fetching task %s: %s", task_id, exc)
            return None

    def _fetch_subtasks_from_parent(self, parent: dict[str, Any]) -> list[dict[str, Any]]:
        """Return subtasks embedded on GET /me/tasks/{id}.

        Do not use GET /me/tasks?parentGlobalTaskId=… — that query param is ignored and returns
        the entire account (~3 MB). The single-task response includes subTasks (pending + completed).
        """
        subtasks = parent.get("subTasks") or []
        return [subtask for subtask in subtasks if isinstance(subtask, dict)]

    def _fetch_attachments_via_api(self, task_id: str) -> list[dict[str, Any]]:
        """Fetch attachment rows via GET /me/attachments?globalTaskId=…

        Per-task query avoids pulling the attachment model from full sync. Agent export does not
        include attachments at all.
        """
        if not self.logged_in:
            return []
        try:
            response = self.session.get(
                f"{self.base_url}/me/attachments",
                params={"globalTaskId": task_id},
                timeout=AuthConstants.REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                logger.warning("Failed to fetch attachments for %s: HTTP %d", task_id, response.status_code)
                return []
            body = response.json()
            if not isinstance(body, list):
                return []
            return [item for item in body if isinstance(item, dict) and not item.get("deleted")]
        except requests.RequestException as exc:
            logger.warning("Error fetching attachments for %s: %s", task_id, exc)
            return []

    def _fetch_task_bundle(self, task_id: str) -> dict[str, Any] | None:
        """Build a minimal sync-shaped payload for one task (+ subtasks + attachments).

        Shapes the REST responses like a bg_sync delta so get_task/get_subtasks/get_attachments
        can reuse the same code paths as bulk sync data.
        """
        parent = self._fetch_task_via_api(task_id)
        if not parent:
            return None
        subtasks = self._fetch_subtasks_from_parent(parent)
        parent_record = {key: value for key, value in parent.items() if key != "subTasks"}
        attachments = self._fetch_attachments_via_api(task_id)
        return {
            "models": {
                "task": {"items": [parent_record, *subtasks]},
                "attachment": {"items": attachments},
            }
        }

    def _resolve_clone_tasks_data(
        self,
        task_id: str,
        tasks_data: dict[str, Any] | None,
        *,
        include_attachments: bool = True,
    ) -> dict[str, Any] | None:
        """Choose the lightest data source that has enough to clone one task.

        1. Caller-supplied sync-shaped tasks_data when it already contains the source task.
        2. Otherwise REST task bundle (~few KB) — no full sync, no agent export (wrong shape).
        3. If tasks_data has the task but no attachment rows, fetch attachments only for that id.
        """
        if tasks_data and self.get_task(task_id, tasks_data):
            if include_attachments and not any(
                attachment.get("globalTaskId") == task_id or attachment.get("global_task_id") == task_id
                for attachment in self.get_attachments(tasks_data)
            ):
                fetched = self._fetch_attachments_via_api(task_id)
                if fetched:
                    merged = dict(tasks_data)
                    models = dict(merged.get("models") or {})
                    attachment_model = dict(models.get("attachment") or {})
                    existing = list(attachment_model.get("items") or [])
                    attachment_model["items"] = [*existing, *fetched]
                    models["attachment"] = attachment_model
                    merged["models"] = models
                    return merged
            return tasks_data

        logger.info("Fetching task bundle via REST for clone: %s", task_id)
        return self._fetch_task_bundle(task_id)

    def _put_create_task(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Create one task via PUT /me/tasks.

        New-row creates persist reliably on cookie sessions; this is why clone/recreate work around
        broken in-place updates (see _mutate_tasks).
        """
        if not self.logged_in:
            logger.warning("Not logged in")
            return None
        try:
            url = f"{self.base_url}/me/tasks"
            response = self.session.put(url, json=[payload], timeout=AuthConstants.REQUEST_TIMEOUT)
            if response.status_code != 200:
                logger.warning("Failed to create task: HTTP %d", response.status_code)
                return None
            created = response.json()
            if isinstance(created, list) and created:
                self._note_mutation()
                return created[0]
            return payload
        except requests.RequestException as exc:
            logger.error("Error creating task: %s", exc)
            return None

    def create_task(
        self,
        title: str,
        *,
        category_id: str | None = None,
        note: str = "",
        labels: list[str] | None = None,
        priority: str = "Normal",
        due_date: int = 0,
        alert: dict[str, Any] | None = None,
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
            alert: Optional reminder dict (``type``/``offset``/``repeatEndType``).

        Returns:
            The created task dict from the API, or None on failure.
        """
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        payload = self._build_new_task_payload(
            title,
            category_id=category_id,
            note=note,
            labels=labels,
            priority=priority,
            due_date=due_date,
            alert=alert,
        )
        created = self._put_create_task(payload)
        if created:
            logger.info("Created task: %s (%s)", title, created.get("id"))
        return created

    def delete_task(self, task_id: str, *, force: bool = False, tasks_data: dict[str, Any] | None = None) -> bool:
        """Delete a task by its ID. Returns True if the task was deleted (HTTP 204).

        Unless ``force`` is True, refuses to delete tasks that still have a note or
        subtasks (logs a warning and returns False). Callers merging tasks should
        migrate note/subtask content to the parent first, then delete with force.
        """
        if not self.logged_in:
            logger.warning("Not logged in")
            return False

        task = self.get_task(task_id, tasks_data)
        if task and not force:
            note = (task.get("note") or "").strip()
            subtasks = self.get_subtasks(task_id, tasks_data)
            if note or subtasks:
                logger.warning(
                    "Refusing to delete task %s (%r): note=%s subtasks=%d — "
                    "migrate content first or pass force=True",
                    task_id,
                    task.get("title"),
                    bool(note),
                    len(subtasks),
                )
                return False

        try:
            url = f"{self.base_url}/me/tasks/{task_id}"
            response = self.session.delete(url, timeout=AuthConstants.REQUEST_TIMEOUT)

            if response.status_code == 204:
                logger.info("Deleted task %s", task_id)
                self._note_mutation()
                return True

            logger.warning("Failed to delete task %s: HTTP %d", task_id, response.status_code)
            return False

        except requests.RequestException as e:
            logger.error("Error deleting task %s: %s", task_id, e)
            return False

    def _build_mutation_payload(self, task_id: str, **fields: Any) -> dict[str, Any]:
        """Build a partial task mutation payload with per-field update timestamps."""
        now = int(time.time() * 1000)
        payload: dict[str, Any] = {
            "globalTaskId": task_id,
            "id": task_id,
            "lastUpdateDate": now,
        }

        for field_name, value in fields.items():
            if field_name not in _TASK_MUTATION_FIELDS:
                raise ValueError(f"Unsupported task mutation field: {field_name}")
            api_field, update_time_field = _TASK_MUTATION_FIELDS[field_name]
            payload[api_field] = value
            payload[update_time_field] = now

        return payload

    def _put_tasks(self, payloads: list[dict[str, Any]], *, tasks_data: dict[str, Any] | None = None) -> bool:
        """Send task field mutations (sync push when possible, else verified PUT)."""
        return self._mutate_tasks(payloads, tasks_data=tasks_data)

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        note: str | None = None,
        status: str | None = None,
        category_id: str | None = None,
        due_date: int | None = None,
        labels: list[str] | None = None,
        priority: str | None = None,
        alert: dict[str, Any] | None = None,
        tasks_data: dict[str, Any] | None = None,
    ) -> bool:
        """Update one or more fields on an existing task.

        Title, note, due date, reminders (``alert``), labels, priority, list moves, and
        reorder/position use Any.do's sync engine (``POST /api/v14/me/sync``). The client
        attempts that path first, then falls back to ``PUT /me/tasks`` with response
        verification. Returns ``False`` if the server echoes stale values — common for
        cookie-only sessions without ``X-Anydo-Auth``. Prefer the web UI for those edits
        until sync push is fully working, or use ``create_subtask`` / ``delete_task`` for
        structural changes (those paths work today).
        """
        fields: dict[str, Any] = {}
        if title is not None:
            fields["title"] = title
        if note is not None:
            fields["note"] = note
        if status is not None:
            fields["status"] = status
        if category_id is not None:
            fields["category_id"] = category_id
        if due_date is not None:
            fields["due_date"] = due_date
        if labels is not None:
            fields["labels"] = labels
        if priority is not None:
            fields["priority"] = priority
        if alert is not None:
            fields["alert"] = alert

        if not fields:
            logger.warning("update_task called with no fields to update")
            return False

        payload = self._build_mutation_payload(task_id, **fields)
        return self._put_tasks([payload], tasks_data=tasks_data)

    def complete_task(self, task_id: str) -> bool:
        """Mark a task as completed (status CHECKED)."""
        return self.update_task(task_id, status="CHECKED")

    def uncomplete_task(self, task_id: str) -> bool:
        """Revert a completed task to active (status UNCHECKED)."""
        return self.update_task(task_id, status="UNCHECKED")

    def archive_task(self, task_id: str) -> bool:
        """Archive a task (status DONE)."""
        return self.update_task(task_id, status="DONE")

    def move_task(self, task_id: str, category_id: str) -> bool:
        """Move a task to a different list."""
        return self.update_task(task_id, category_id=category_id)

    def set_due_date(
        self,
        task_id: str,
        due_date_ms: int,
        *,
        reminder_offset: int | None = None,
        tasks_data: dict[str, Any] | None = None,
    ) -> bool:
        """Set a task due date and optionally configure a reminder."""
        fields: dict[str, Any] = {"due_date": due_date_ms}
        if reminder_offset is not None:
            fields["alert"] = {
                "type": "OFFSET",
                "offset": reminder_offset,
                "customTime": 0,
                "repeatEndType": "REPEAT_END_NEVER",
            }
        payload = self._build_mutation_payload(task_id, **fields)
        return self._put_tasks([payload], tasks_data=tasks_data)

    def set_labels(self, task_id: str, label_ids: list[str]) -> bool:
        """Replace the tags on a task."""
        return self.update_task(task_id, labels=label_ids)

    def set_priority(self, task_id: str, priority: str) -> bool:
        """Set task priority (Normal, High, or Low)."""
        return self.update_task(task_id, priority=priority)

    def create_subtask(
        self,
        parent_id: str,
        title: str,
        *,
        note: str = "",
        category_id: str | None = None,
        due_date: int = 0,
        labels: list[str] | None = None,
        priority: str = "Normal",
        alert: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Create a subtask under an existing parent task."""
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        if category_id is None:
            parent = self.get_task(parent_id)
            if parent:
                category_id = parent.get("categoryId")

        payload = self._build_new_task_payload(
            title,
            category_id=category_id,
            note=note,
            labels=labels,
            priority=priority,
            due_date=due_date,
            alert=alert,
            parent_id=parent_id,
        )
        created = self._put_create_task(payload)
        if created:
            logger.info("Created subtask: %s (%s)", title, created.get("id"))
        return created

    def clone_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        tasks_data: dict[str, Any] | None = None,
        delete_source: bool = False,
        include_subtasks: bool = True,
        include_attachments: bool = True,
    ) -> dict[str, Any] | None:
        """Clone a task by creating a new one with the same fields (create path, not update).

        Use when ``update_task()`` cannot change title/note/due/reminder in place. New tasks
        created via ``PUT /me/tasks`` persist title, note, due date, reminders, tags, priority,
        subtasks (including completed), and attachment links; mutating existing rows often does not.

        Args:
            task_id: Source task ID.
            title: Optional title override (typical rename workaround).
            tasks_data: Optional synced task payload to avoid an extra pull.
            delete_source: Delete the source task after a successful clone (rename pattern).
            include_subtasks: Recreate all subtasks under the new parent (any status).
            include_attachments: Re-register attachment rows pointing at the same file URLs.

        Returns:
            The newly created parent task dict, or None on failure. When ``delete_source`` is
            True and deletion fails, the clone is kept and a warning is logged.

        Note:
            ``globalTaskId`` always changes. List position and creation date are best-effort on
            create (usually ignored). See ``_resolve_clone_tasks_data`` for why we fetch via REST
            instead of full sync when ``tasks_data`` is omitted.
        """
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        tasks_data = self._resolve_clone_tasks_data(
            task_id,
            tasks_data,
            include_attachments=include_attachments,
        )
        if not tasks_data:
            logger.warning("clone_task: could not load source task data for %s", task_id)
            return None

        source = self.get_task(task_id, tasks_data)
        if not source:
            logger.warning("clone_task: source task not found: %s", task_id)
            return None

        parent = self._put_create_task(
            self._build_new_task_payload_from_record(
                source,
                title=title,
            )
        )
        if not parent:
            return None

        new_parent_id = parent.get("globalTaskId") or parent.get("id")
        if include_subtasks and new_parent_id:
            for sub in self.get_subtasks(task_id, tasks_data):
                sub_payload = self._build_new_task_payload_from_record(
                    sub,
                    parent_id=new_parent_id,
                )
                if self._put_create_task(sub_payload) is None:
                    logger.warning(
                        "clone_task: failed to clone subtask %r under %s",
                        sub.get("title"),
                        new_parent_id,
                    )

        if include_attachments and new_parent_id:
            self._clone_attachments(task_id, new_parent_id, tasks_data)

        if delete_source:
            if not self.delete_task(task_id, force=True, tasks_data=tasks_data):
                logger.warning(
                    "clone_task: created %s but failed to delete source %s — both exist",
                    new_parent_id,
                    task_id,
                )

        return parent

    def recreate_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        tasks_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Clone a task and delete the original (rename / replace workaround).

        Equivalent to ``clone_task(..., delete_source=True)``. Returns the new task dict;
        the new ``globalTaskId`` differs from the source.
        """
        return self.clone_task(
            task_id,
            title=title,
            tasks_data=tasks_data,
            delete_source=True,
        )

    def complete_subtask(self, subtask_id: str) -> bool:
        """Mark a subtask as completed."""
        return self.complete_task(subtask_id)

    def get_completed_tasks(self, page: int = 0) -> dict[str, Any] | None:
        """Fetch paginated completed task history."""
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        try:
            url = f"{self.base_url}/me/completed_tasks"
            response = self.session.get(url, params={"page": page}, timeout=AuthConstants.REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.json()
            logger.warning("Failed to fetch completed tasks: HTTP %d", response.status_code)
            return None
        except requests.RequestException as e:
            logger.error("Error fetching completed tasks: %s", e)
            return None

    def _get_task_items(self, tasks_data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return raw task items from sync data."""
        if tasks_data is None:
            tasks_data = self.get_tasks()
        if not tasks_data:
            return []

        if "models" in tasks_data and "task" in tasks_data["models"]:
            return list(tasks_data["models"]["task"].get("items", []))

        if "tasks" in tasks_data:
            return list(tasks_data["tasks"])

        return []

    @staticmethod
    def _task_due_ms(task: dict[str, Any]) -> int | None:
        """Return due date in milliseconds, or None if unset."""
        due_date = task.get("dueDate")
        if due_date in (None, 0, ""):
            return None
        try:
            return int(due_date)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _task_creation_ms(task: dict[str, Any]) -> int | None:
        """Return creation date in milliseconds, or None if unset."""
        creation_date = task.get("creationDate")
        if creation_date in (None, 0, ""):
            return None
        try:
            return int(creation_date)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _start_of_day_ms(when: datetime | None = None) -> int:
        """Return local midnight for the given day as milliseconds since epoch."""
        current = when or datetime.now()
        midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(midnight.timestamp() * 1000)

    def get_task(self, task_id: str, tasks_data: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Look up a single task by globalTaskId."""
        for task in self._get_task_items(tasks_data):
            if task.get("globalTaskId") == task_id or task.get("id") == task_id:
                return task
        return None

    def get_subtasks(self, parent_id: str, tasks_data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return all subtasks for a parent task."""
        return [
            task
            for task in self._get_task_items(tasks_data)
            if task.get("parentGlobalTaskId") == parent_id
        ]

    def find_tasks(
        self,
        *,
        query: str | None = None,
        list_name: str | None = None,
        tag_name: str | None = None,
        status: str | None = None,
        due_before: int | None = None,
        due_after: int | None = None,
        tasks_data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter tasks from sync data using common agent-friendly criteria."""
        if tasks_data is None:
            tasks_data = self.get_tasks()
        if not tasks_data:
            return []

        category_id = self.get_category_id(list_name, tasks_data) if list_name else None
        label_id = self.get_label_id(tag_name, tasks_data) if tag_name else None

        results: list[dict[str, Any]] = []
        for task in self._get_task_items(tasks_data):
            if query and query.lower() not in task.get("title", "").lower():
                continue
            if category_id and task.get("categoryId") != category_id:
                continue
            if label_id:
                labels = task.get("labels") or []
                if label_id not in labels:
                    continue
            if status and task.get("status") != status:
                continue

            due_ms = self._task_due_ms(task)
            if due_before is not None and (due_ms is None or due_ms > due_before):
                continue
            if due_after is not None and (due_ms is None or due_ms < due_after):
                continue

            results.append(task)

        return results

    def get_overdue_tasks(self, tasks_data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return active tasks with a due date before today."""
        now_ms = int(time.time() * 1000)
        return self.find_tasks(status="UNCHECKED", due_before=now_ms, tasks_data=tasks_data)

    def get_tasks_due_today(self, tasks_data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return active tasks due today (local timezone)."""
        start_ms = self._start_of_day_ms()
        end_ms = int((datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp() * 1000)
        tasks = self.find_tasks(status="UNCHECKED", due_after=start_ms - 1, due_before=end_ms, tasks_data=tasks_data)
        return [task for task in tasks if self._task_due_ms(task) is not None and start_ms <= self._task_due_ms(task) < end_ms]

    def _put_categories(self, payloads: list[dict[str, Any]]) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Send category/list mutations via PUT /me/categories."""
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        try:
            url = f"{self.base_url}/me/categories"
            response = self.session.put(url, json=payloads, timeout=AuthConstants.REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.json()
            logger.warning("Failed to update categories: HTTP %d", response.status_code)
            return None
        except requests.RequestException as e:
            logger.error("Error updating categories: %s", e)
            return None

    def create_list(self, name: str) -> dict[str, Any] | None:
        """Create a new task list."""
        now = int(time.time() * 1000)
        list_id = uuid.uuid4().hex[:24]
        payload = {
            "id": list_id,
            "name": name,
            "position": "1000",
            "isDefault": False,
            "isDeleted": False,
            "isGroceryList": False,
            "lastUpdateDate": now,
        }
        result = self._put_categories([payload])
        if result is None:
            return None
        if isinstance(result, list) and result:
            return result[0]
        return payload

    def rename_list(self, list_id: str, name: str) -> bool:
        """Rename an existing list."""
        now = int(time.time() * 1000)
        payload = {"id": list_id, "name": name, "lastUpdateDate": now}
        return self._put_categories([payload]) is not None

    def delete_list(self, list_id: str) -> bool:
        """Soft-delete a list."""
        now = int(time.time() * 1000)
        payload = {"id": list_id, "isDeleted": True, "lastUpdateDate": now}
        return self._put_categories([payload]) is not None

    def _put_labels(self, payloads: list[dict[str, Any]]) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Send label/tag mutations via PUT /me/labels."""
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        try:
            url = f"{self.base_url}/me/labels"
            response = self.session.put(url, json=payloads, timeout=AuthConstants.REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.json()
            logger.warning("Failed to update labels: HTTP %d", response.status_code)
            return None
        except requests.RequestException as e:
            logger.error("Error updating labels: %s", e)
            return None

    def get_tags(self, tasks_data: dict[str, Any] | None = None) -> list[TagInfo]:
        """Get all tags/labels from sync data."""
        if tasks_data is None:
            tasks_data = self.get_tasks()
        if not tasks_data:
            return []

        tags: list[TagInfo] = []
        if "models" in tasks_data and "label" in tasks_data["models"]:
            for label in tasks_data["models"]["label"].get("items", []):
                if label.get("isDeleted"):
                    continue
                tags.append(
                    {
                        "id": label.get("id", ""),
                        "name": label.get("name", ""),
                        "color": label.get("color", ""),
                        "is_deleted": label.get("isDeleted", False),
                        "is_predefined": label.get("isPredefined", False),
                    }
                )
        return tags

    def create_tag(self, name: str, color: str = "#ff6168") -> dict[str, Any] | None:
        """Create a new tag/label."""
        now = int(time.time() * 1000)
        tag_id = uuid.uuid4().hex[:24]
        payload = {
            "id": tag_id,
            "name": name,
            "color": color,
            "isDeleted": False,
            "isPredefined": False,
            "lastUpdateDate": now,
        }
        result = self._put_labels([payload])
        if result is None:
            return None
        if isinstance(result, list) and result:
            return result[0]
        return payload

    def rename_tag(self, tag_id: str, name: str) -> bool:
        """Rename an existing tag."""
        now = int(time.time() * 1000)
        payload = {"id": tag_id, "name": name, "lastUpdateDate": now}
        return self._put_labels([payload]) is not None

    def delete_tag(self, tag_id: str) -> bool:
        """Soft-delete a tag."""
        now = int(time.time() * 1000)
        payload = {"id": tag_id, "isDeleted": True, "lastUpdateDate": now}
        return self._put_labels([payload]) is not None

    def get_attachments(self, tasks_data: dict[str, Any] | None = None) -> list[AttachmentInfo]:
        """Get all attachments from sync data."""
        if tasks_data is None:
            tasks_data = self.get_tasks()
        if not tasks_data:
            return []

        attachments: list[AttachmentInfo] = []
        if "models" in tasks_data and "attachment" in tasks_data["models"]:
            for attachment in tasks_data["models"]["attachment"].get("items", []):
                if attachment.get("deleted"):
                    continue
                attachments.append(
                    {
                        "id": attachment.get("id", ""),
                        "global_task_id": attachment.get("globalTaskId", ""),
                        "display_name": attachment.get("displayName", ""),
                        "mime_type": attachment.get("mimeType", ""),
                        "file_size": attachment.get("fileSize", 0),
                        "url": attachment.get("url", ""),
                        "deleted": attachment.get("deleted", False),
                        "creation_date": attachment.get("creationDate"),
                        "last_update_date": attachment.get("lastUpdateDate"),
                    }
                )
        return attachments

    def get_upload_url(self, filename: str, mime_type: str | None = None) -> dict[str, Any] | None:
        """Request a presigned S3 POST for uploading an attachment."""
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        resolved_mime = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        try:
            url = f"{self.base_url}/me/request_s3_presigned_post"
            params = {
                "S3ObjectType": resolved_mime,
                "S3ObjectName": filename,
                "UploadType": "attachment",
            }
            response = self.session.get(url, params=params, timeout=AuthConstants.REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.json()
            logger.warning("Failed to get upload URL: HTTP %d", response.status_code)
            return None
        except requests.RequestException as e:
            logger.error("Error getting upload URL: %s", e)
            return None

    def upload_attachment(self, task_id: str, filepath: str | Path) -> str | None:
        """Upload a file to S3 and register it against a task."""
        if not self.logged_in:
            logger.warning("Not logged in")
            return None

        path = Path(filepath)
        if not path.is_file():
            logger.warning("Attachment file not found: %s", path)
            return None

        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        presign = self.get_upload_url(path.name, mime_type)
        if not presign:
            return None

        upload_url = presign.get("url")
        fields = presign.get("fields", {})
        if not upload_url or "key" not in fields:
            logger.warning("Invalid presigned upload response")
            return None

        try:
            with path.open("rb") as file_handle:
                response = requests.post(
                    upload_url,
                    data=fields,
                    files={"file": (path.name, file_handle, mime_type)},
                    timeout=AuthConstants.REQUEST_TIMEOUT,
                )
            if response.status_code not in (200, 201, 204):
                logger.warning("Failed to upload attachment to S3: HTTP %d", response.status_code)
                return None

            final_url = f"{upload_url.rstrip('/')}/{fields['key']}"
            if not self._register_attachment(
                task_id,
                display_name=path.name,
                mime_type=mime_type,
                file_size=path.stat().st_size,
                url=final_url,
            ):
                logger.warning("Uploaded file but failed to register attachment")
            return final_url
        except (OSError, requests.RequestException) as e:
            logger.error("Error uploading attachment: %s", e)
            return None

    def download_attachment(self, url: str, dest_path: str | Path) -> bool:
        """Download an attachment from a public URL."""
        destination = Path(dest_path)
        try:
            response = requests.get(url, timeout=AuthConstants.REQUEST_TIMEOUT)
            if response.status_code != 200:
                logger.warning("Failed to download attachment: HTTP %d", response.status_code)
                return False
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(response.content)
            return True
        except (OSError, requests.RequestException) as e:
            logger.error("Error downloading attachment: %s", e)
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

            latest_raw_path = os.path.join("outputs/raw-json", "latest.json")
            with open(latest_raw_path, "w", encoding="utf-8") as f:
                json.dump(tasks_data, f, indent=2, ensure_ascii=False)

            self.last_data_hash = current_hash

            file_size = os.path.getsize(filepath)
            size_mb = file_size / (1024 * 1024)

            logger.info("Tasks exported to: %s (%.2f MB)", filepath, size_mb)

            self._save_markdown_from_json(tasks_data, timestamp)
            self._save_agent_export(tasks_data, timestamp)

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

            latest_path = os.path.join("outputs/markdown", "latest.md")
            with open(latest_path, "w", encoding="utf-8") as f:
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
                lines.append("| Title | List | Tags | Created | Due | Priority | Assignee | Note |")
                lines.append("|-------|------|------|---------|-----|----------|----------|------|")
            else:
                lines.append("| Title | List | Tags | Created | Due | Note |")
                lines.append("|-------|------|------|---------|-----|------|")

            for task in sorted_tasks:
                status_emoji = self._get_status_emoji(task, verbose)
                title = self._format_task_title(task)
                list_name = task.get("list_name", "Unknown")

                created_full = task.get("created_date", "N/A")
                created = created_full.split(" ")[0] if created_full != "N/A" and " " in created_full else created_full

                due = task.get("due_date", "")

                title_cell = f"{status_emoji}{title}" if status_emoji else title

                note = task.get("note")
                note_cell = ""
                if note and note.strip():
                    note_cell = self._wrap_text(note.strip(), markdown_safe=True)

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

                tags_display = ", ".join(task.get("tags", []))

                if verbose:
                    priority = task.get("priority", "normal")
                    priority_emoji = self._get_priority_emoji(priority)
                    assignee = task.get("assignee", "")
                    assignee_display = f"👤 {assignee}" if assignee else ""

                    lines.append(
                        f"| {title_cell} | {list_name} | {tags_display} | 📅 {created} | {due} | "
                        f"{priority_emoji} {priority} | {assignee_display} | {note_cell} |"
                    )
                else:
                    due_display = f"⏰ {due}" if due else ""
                    lines.append(
                        f"| {title_cell} | {list_name} | {tags_display} | 📅 {created} | {due_display} | {note_cell} |"
                    )

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

            # Build category and label lookup dicts once
            category_lookup: dict[str, dict[str, Any]] = {}
            if "models" in tasks_data and "category" in tasks_data["models"]:
                for cat in tasks_data["models"]["category"]["items"]:
                    category_lookup[cat.get("id", "")] = cat

            label_lookup: dict[str, str] = {}
            if "models" in tasks_data and "label" in tasks_data["models"]:
                for label in tasks_data["models"]["label"]["items"]:
                    if not label.get("isDeleted"):
                        label_lookup[label.get("id", "")] = label.get("name", label.get("id", ""))

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
                        task_info["tags"] = [label_lookup.get(label_id, label_id) for label_id in task["labels"]]

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

    def _extract_agent_data(self, tasks_data: dict[str, Any]) -> AgentExportInfo:
        """Extract a compact, token-efficient task snapshot for agents."""
        category_lookup: dict[str, str] = {}
        if "models" in tasks_data and "category" in tasks_data["models"]:
            for cat in tasks_data["models"]["category"]["items"]:
                if not cat.get("isDeleted"):
                    category_lookup[cat.get("id", "")] = cat.get("name", "Unknown List")

        label_lookup: dict[str, str] = {}
        if "models" in tasks_data and "label" in tasks_data["models"]:
            for label in tasks_data["models"]["label"]["items"]:
                if not label.get("isDeleted"):
                    label_lookup[label.get("id", "")] = label.get("name", label.get("id", ""))

        def build_task(task: dict[str, Any]) -> AgentTaskInfo:
            cat_id = task.get("categoryId", "")
            label_ids = task.get("labels") or []
            task_id = task.get("globalTaskId") or task.get("id", "")
            record: AgentTaskInfo = {
                "id": task_id,
                "title": task.get("title", "Untitled Task"),
                "list_id": cat_id,
                "list": category_lookup.get(cat_id, "Unknown List"),
            }
            if label_ids:
                record["tag_ids"] = label_ids
                record["tags"] = [label_lookup.get(label_id, label_id) for label_id in label_ids]
            due_ms = self._task_due_ms(task)
            if due_ms is not None:
                record["due_ms"] = due_ms
            creation_ms = self._task_creation_ms(task)
            if creation_ms is not None:
                record["creation_ms"] = creation_ms
            note = (task.get("note") or "").strip()
            if note:
                record["note"] = note
            return record

        pending_by_id: dict[str, AgentTaskInfo] = {}
        subtasks_by_parent: dict[str, list[AgentTaskInfo]] = {}

        if "models" in tasks_data and "task" in tasks_data["models"]:
            for task in tasks_data["models"]["task"]["items"]:
                if task.get("status") != "UNCHECKED":
                    continue
                task_id = task.get("globalTaskId") or task.get("id", "")
                parent_id = task.get("parentGlobalTaskId")
                record = build_task(task)
                if parent_id:
                    subtasks_by_parent.setdefault(parent_id, []).append(record)
                else:
                    pending_by_id[task_id] = record

        for parent_id, subtasks in subtasks_by_parent.items():
            if parent_id in pending_by_id:
                pending_by_id[parent_id]["subtasks"] = sorted(subtasks, key=lambda item: item.get("title", ""))

        tasks = sorted(pending_by_id.values(), key=lambda item: item.get("title", ""))

        return {
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_sync_timestamp": self.last_sync_timestamp,
            "last_mutation_timestamp": self.last_mutation_timestamp,
            "sync_stale": self._sync_is_stale(),
            "pending_tasks": len(tasks),
            "lists": [{"id": cat_id, "name": name} for cat_id, name in sorted(category_lookup.items(), key=lambda x: x[1])],
            "tags": [{"id": tag_id, "name": name} for tag_id, name in sorted(label_lookup.items(), key=lambda x: x[1])],
            "tasks": tasks,
        }

    def _save_agent_export(self, tasks_data: dict[str, Any], timestamp: str) -> str | None:
        """Write compact agent-friendly JSON export."""
        try:
            agent_data = self._extract_agent_data(tasks_data)
            if not agent_data.get("tasks") and not agent_data.get("lists"):
                logger.info("No pending tasks for agent export - skipping")
                return None

            os.makedirs("outputs/agent", exist_ok=True)
            filename = f"{timestamp}_tasks.json"
            filepath = os.path.join("outputs/agent", filename)
            latest_path = os.path.join("outputs/agent", "latest.json")

            payload = json.dumps(agent_data, indent=2, ensure_ascii=False)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(payload)
            with open(latest_path, "w", encoding="utf-8") as f:
                f.write(payload)

            size_kb = len(payload.encode("utf-8")) / 1024
            logger.info("Agent export written to: %s and latest.json (%.1f KB)", filepath, size_kb)
            return filepath
        except OSError as e:
            logger.error("Error saving agent export: %s", e)
            return None

    @staticmethod
    def get_latest_export_path(kind: str = "agent") -> str | None:
        """
        Return the path to the latest export file for agents.

        Kinds: ``agent`` (compact JSON), ``markdown``, ``raw-json``.
        """
        latest_names = {
            "agent": os.path.join("outputs", "agent", "latest.json"),
            "markdown": os.path.join("outputs", "markdown", "latest.md"),
            "raw-json": os.path.join("outputs", "raw-json", "latest.json"),
        }
        latest = latest_names.get(kind)
        if latest and os.path.exists(latest):
            return latest

        directory = os.path.dirname(latest) if latest else None
        if not directory or not os.path.isdir(directory):
            return None

        extensions = {"agent": ".json", "markdown": ".md", "raw-json": ".json"}
        suffix = extensions.get(kind, "")
        candidates = sorted(
            (path for path in Path(directory).glob(f"*{suffix}") if path.name != f"latest{suffix}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return str(candidates[0]) if candidates else None

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
