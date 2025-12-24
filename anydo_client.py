"""
Any.do API Client

A Python client for the Any.do API with session persistence, 2FA support,
and efficient sync strategies.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import time
import os
import hashlib
import textwrap
import uuid
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, TypedDict

# Configure module logger
logger = logging.getLogger(__name__)

# Public API
__all__ = ['AnyDoClient', 'TaskInfo', 'ListInfo', 'ExportInfo']


# =============================================================================
# Constants
# =============================================================================

class APIConstants:
    """API-related constants."""
    BASE_URL = "https://sm-prod4.any.do"
    API_VERSION = "5.0.97"
    PLATFORM = "web"
    X_PLATFORM = "3"


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
    id: Optional[str]
    parent_id: Optional[str]
    created_date: str
    last_update: str
    due_date: str
    list_name: str
    note: str
    tags: List[str]
    status: str
    priority: str
    list_color: Optional[str]
    assignee: Optional[str]
    repeating: str
    subtasks: List['TaskInfo']
    _internal_status: str


class ListInfo(TypedDict, total=False):
    """Type definition for list/category information."""
    id: str
    name: str
    color: Optional[str]
    is_default: bool
    position: Optional[int]
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

    def __init__(self, session_file: str = "session.json", text_wrap_width: int = 80):
        """
        Initialize the Any.do client.

        Args:
            session_file: Path to the session persistence file
            text_wrap_width: Width for text wrapping in exports
        """
        self.session = requests.Session()
        self.base_url = APIConstants.BASE_URL
        self.logged_in = False
        self.user_info: Optional[Dict[str, Any]] = None
        self.session_file = session_file
        self.last_data_hash: Optional[str] = None
        self.last_pretty_hash: Optional[str] = None
        self.text_wrap_width = text_wrap_width
        self.last_sync_timestamp: Optional[int] = None
        self.last_full_sync_timestamp: Optional[int] = None
        self.client_id = str(uuid.uuid4())
        self.auth_token: Optional[str] = None

        # Configure retry strategy with exponential backoff
        retry_strategy = Retry(
            total=RetryConstants.MAX_RETRIES,
            backoff_factor=RetryConstants.BACKOFF_FACTOR,
            status_forcelist=RetryConstants.STATUS_FORCELIST,
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST", "PUT"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        # Set headers to match browser requests
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-GB,en-US;q=0.9,en;q=0.8,pl;q=0.7,no;q=0.6',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Content-Type': 'application/json; charset=UTF-8',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
            'X-Anydo-Platform': APIConstants.PLATFORM,
            'X-Anydo-Version': APIConstants.API_VERSION,
            'X-Platform': APIConstants.X_PLATFORM
        })

        # Try to load existing session
        self._load_session()

    def _load_session(self) -> bool:
        """Load existing session from file if available."""
        if os.path.exists(self.session_file):
            try:
                with open(self.session_file, 'r') as f:
                    session_data = json.load(f)

                # Restore cookies
                for cookie_data in session_data.get('cookies', []):
                    self.session.cookies.set(
                        cookie_data['name'],
                        cookie_data['value'],
                        domain=cookie_data.get('domain'),
                        path=cookie_data.get('path', '/')
                    )

                self.user_info = session_data.get('user_info')
                self.last_data_hash = session_data.get('last_data_hash')
                self.last_pretty_hash = session_data.get('last_pretty_hash')
                self.last_sync_timestamp = session_data.get('last_sync_timestamp')
                self.last_full_sync_timestamp = session_data.get('last_full_sync_timestamp')

                user_email = self.user_info.get('email', 'unknown user') if self.user_info else 'unknown user'
                logger.info("Loaded existing session for %s", user_email)
                print(f"📱 Loaded existing session for {user_email}")

                # Test if session is still valid
                if self._test_session():
                    self.logged_in = True
                    logger.info("Session is still valid")
                    print("✅ Session is still valid")
                    return True
                else:
                    logger.warning("Session expired, will need to login again")
                    print("⚠️  Session expired, will need to login again")
                    self._clear_session()
                    return False

            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Error loading session: %s", e)
                print(f"⚠️  Error loading session: {e}")
                self._clear_session()
                return False
            except OSError as e:
                logger.warning("Error reading session file: %s", e)
                print(f"⚠️  Error reading session file: {e}")
                self._clear_session()
                return False
        return False

    def _save_session(self) -> None:
        """Save current session to file."""
        try:
            session_data = {
                'cookies': [
                    {
                        'name': cookie.name,
                        'value': cookie.value,
                        'domain': cookie.domain,
                        'path': cookie.path
                    }
                    for cookie in self.session.cookies
                ],
                'user_info': self.user_info,
                'saved_at': datetime.now().isoformat(),
                'last_data_hash': self.last_data_hash,
                'last_pretty_hash': self.last_pretty_hash,
                'last_sync_timestamp': self.last_sync_timestamp,
                'last_full_sync_timestamp': self.last_full_sync_timestamp
            }

            with open(self.session_file, 'w') as f:
                json.dump(session_data, f, indent=2)

            logger.info("Session saved successfully")
            print("💾 Session saved successfully")

        except (OSError, TypeError) as e:
            logger.error("Error saving session: %s", e)
            print(f"⚠️  Error saving session: {e}")

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

    def login(self, email: str, password: str) -> bool:
        """
        Login to Any.do with email and password.

        Args:
            email: Your Any.do email address
            password: Your Any.do password

        Returns:
            bool: True if login successful, False otherwise
        """
        # If already logged in with valid session, return success
        if self.logged_in and self._test_session():
            logger.info("Already logged in with valid session")
            print("✅ Already logged in with valid session")
            return True

        try:
            # Store credentials for the login process
            self._temp_email = email
            self._temp_password = password

            # Step 1: Check if email exists in system
            logger.info("Checking email...")
            print("🔐 Checking email...")
            check_email_url = f"{self.base_url}/check_email"
            check_email_data = {"email": email}

            # Add delay to prevent rate limiting
            time.sleep(AuthConstants.LOGIN_DELAY_SECONDS)
            response = self.session.post(
                check_email_url,
                json=check_email_data,
                timeout=AuthConstants.REQUEST_TIMEOUT
            )

            if response.status_code == 200:
                email_data = response.json()
                if not email_data.get('user_exists', False):
                    logger.warning("Email not found in system")
                    print("❌ Email not found in system")
                    return False
                logger.info("Email found in system")
                print("✅ Email found in system")
            else:
                logger.warning("Email check failed: %d, continuing...", response.status_code)
                print(f"⚠️  Email check failed: {response.status_code}, continuing...")

            # Step 2: Attempt 2FA login flow (this is the standard flow for most accounts)
            logger.info("Attempting 2FA login flow...")
            print("🔐 Attempting 2FA login flow...")
            if self._trigger_2fa_email():
                return self._handle_2fa_interactive()
            else:
                logger.error("Failed to trigger 2FA email")
                print("❌ Failed to trigger 2FA email")
                return False

        except requests.RequestException as e:
            logger.error("Login error: %s", e)
            print(f"❌ Login error: {str(e)}")
            return False

    def _handle_2fa_interactive(self) -> bool:
        """Handle 2FA verification with interactive prompts."""

        # First, trigger the 2FA email to be sent
        if not self._trigger_2fa_email():
            logger.error("Failed to trigger 2FA email")
            print("❌ Failed to trigger 2FA email")
            return False

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

                if self._verify_2fa_code(code):
                    self.logged_in = True
                    self._get_user_info()
                    self._save_session()
                    self._cleanup_temp_credentials()
                    return True
                else:
                    remaining = AuthConstants.MAX_2FA_ATTEMPTS - 1 - attempt
                    if remaining > 0:
                        print(f"Invalid code. {remaining} attempts left.")

            except KeyboardInterrupt:
                print("\nCancelled.")
                self._cleanup_temp_credentials()
                return False

        print("Too many failed attempts.")
        self._cleanup_temp_credentials()
        return False

    def _cleanup_temp_credentials(self) -> None:
        """Clean up temporary credentials stored during login."""
        if hasattr(self, '_temp_email'):
            delattr(self, '_temp_email')
        if hasattr(self, '_temp_password'):
            delattr(self, '_temp_password')

    def _trigger_2fa_email(self) -> bool:
        """Trigger 2FA email to be sent using the /login-2fa endpoint."""
        try:
            if not hasattr(self, '_temp_email') or not hasattr(self, '_temp_password'):
                logger.error("Missing credentials for 2FA email trigger")
                print("❌ Missing credentials for 2FA email trigger")
                return False

            logger.info("Triggering 2FA email...")
            print("📧 Triggering 2FA email...")

            login_2fa_url = f"{self.base_url}/login-2fa"
            login_2fa_data = {
                "platform": APIConstants.PLATFORM,
                "referrer": "",
                "requested_experiments": [
                    "AI_FEATURES",
                    "MAC_IN_REVIEW",
                    "WEB_LOCALIZED_PRICING_FEB23",
                    "WEB_OB_AI_MAR_24",
                    "WEB_PREMIUM_TRIAL",
                    "WEB_CALENDAR_QUOTA"
                ],
                "create_predefined_data": {
                    "lists": True,
                    "label": True
                },
                "client_id": self.client_id,
                "locale": "en",
                "email": self._temp_email,
                "password": self._temp_password
            }

            time.sleep(AuthConstants.LOGIN_DELAY_SECONDS)
            response = self.session.post(
                login_2fa_url,
                json=login_2fa_data,
                timeout=AuthConstants.REQUEST_TIMEOUT
            )

            if response.status_code == 200:
                logger.info("2FA email triggered successfully")
                print("✅ 2FA email triggered successfully!")
                return True
            else:
                logger.warning("2FA email trigger returned %d, but continuing...", response.status_code)
                print(f"⚠️  2FA email trigger returned {response.status_code}, but continuing...")
                return True  # Continue even if trigger fails, maybe email was already sent

        except requests.RequestException as e:
            logger.error("Error triggering 2FA email: %s", e)
            print(f"❌ Error triggering 2FA email: {e}")
            return False

    def _verify_2fa_code(self, code: str) -> bool:
        """Verify 2FA code with Any.do servers."""
        try:
            if not hasattr(self, '_temp_email') or not hasattr(self, '_temp_password'):
                logger.error("Missing credentials for 2FA verification")
                print("❌ Missing credentials for 2FA verification")
                return False

            verify_url = f"{self.base_url}/login-2fa-code"
            verify_data = {
                "platform": APIConstants.PLATFORM,
                "referrer": "",
                "requested_experiments": [
                    "AI_FEATURES",
                    "MAC_IN_REVIEW",
                    "WEB_LOCALIZED_PRICING_FEB23",
                    "WEB_OB_AI_MAR_24",
                    "WEB_PREMIUM_TRIAL",
                    "WEB_CALENDAR_QUOTA"
                ],
                "create_predefined_data": {
                    "lists": True,
                    "label": True
                },
                "client_id": self.client_id,
                "locale": "en",
                "email": self._temp_email,
                "code": code,
                "password": self._temp_password
            }

            time.sleep(AuthConstants.VERIFY_DELAY_SECONDS)
            response = self.session.post(
                verify_url,
                json=verify_data,
                timeout=AuthConstants.REQUEST_TIMEOUT
            )

            if response.status_code == 200:
                try:
                    response_data = response.json()

                    if 'auth_token' in response_data:
                        self.auth_token = response_data['auth_token']
                        self.session.headers['X-Anydo-Auth'] = self.auth_token
                        logger.info("2FA verification successful")
                        print("✅ 2FA verification successful!")
                        return True
                    else:
                        logger.error("2FA verification failed - no auth token in response")
                        print("❌ 2FA verification failed - no auth token in response")
                        return False
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("Error parsing 2FA response: %s", e)
                    print(f"❌ Error parsing 2FA response: {e}")
                    # Try to get the auth token from response headers as fallback
                    auth_token = response.headers.get('X-Anydo-Auth')
                    if auth_token:
                        logger.info("Found auth token in response headers")
                        print("✅ Found auth token in response headers!")
                        self.auth_token = auth_token
                        self.session.headers['X-Anydo-Auth'] = auth_token
                        print("✅ 2FA verification successful!")
                        return True
                    else:
                        logger.error("No auth token found in headers either")
                        print("❌ No auth token found in headers either")
                        return False
            else:
                logger.error("2FA verification failed with status: %d", response.status_code)
                print(f"❌ 2FA verification failed with status: {response.status_code}")
                return False

        except requests.RequestException as e:
            logger.error("Error verifying 2FA code: %s", e)
            print(f"❌ Error verifying 2FA code: {e}")
            return False

    def _get_user_info(self) -> bool:
        """Get user information after login."""
        try:
            user_url = f"{self.base_url}/me"
            response = self.session.get(user_url, timeout=AuthConstants.REQUEST_TIMEOUT)

            if response.status_code == 200:
                self.user_info = response.json()
                user_email = self.user_info.get('email', 'Unknown')
                logger.info("Logged in as: %s", user_email)
                print(f"✅ Logged in as: {user_email}")

                # Update timezone to match browser behavior
                self._update_timezone()

                return True
            else:
                logger.warning("Failed to get user info: %d", response.status_code)
                print(f"⚠️  Failed to get user info: {response.status_code}")
                return False

        except requests.RequestException as e:
            logger.error("Error getting user info: %s", e)
            print(f"❌ Error getting user info: {str(e)}")
            return False

    def _update_timezone(self) -> None:
        """Update user timezone to match browser handshake."""
        try:
            # Get local timezone (simplified approach)
            timezone_name = time.tzname[0] if time.tzname[0] else "UTC"

            # Map common timezone names to what Any.do expects
            timezone_mapping = {
                "GMT": "Europe/London",
                "UTC": "UTC",
                "EST": "America/New_York",
                "PST": "America/Los_Angeles",
                "CST": "America/Chicago",
                "MST": "America/Denver"
            }

            timezone_to_send = timezone_mapping.get(timezone_name, timezone_name)

            # Send timezone update
            update_url = f"{self.base_url}/me"
            update_data = {"timezone": timezone_to_send}

            response = self.session.put(
                update_url,
                json=update_data,
                timeout=AuthConstants.REQUEST_TIMEOUT
            )
            if response.status_code == 200:
                logger.info("Timezone updated to: %s", timezone_to_send)
                print(f"✅ Timezone updated to: {timezone_to_send}")
            else:
                logger.warning("Timezone update failed: %d", response.status_code)
                print(f"⚠️  Timezone update failed: {response.status_code}")

        except requests.RequestException as e:
            logger.warning("Error updating timezone: %s", e)
            print(f"⚠️  Error updating timezone: {str(e)}")
            # Don't fail login for timezone update issues

    def get_tasks(self, include_completed: bool = False) -> Optional[Dict[str, Any]]:
        """
        Fetch tasks from Any.do using smart sync strategy.

        Uses incremental sync to detect changes, then performs full sync when changes
        are found (browser-like behavior). Falls back to full sync if incremental fails.

        Args:
            include_completed: Whether to include completed tasks

        Returns:
            Dict containing tasks data, or None if failed
        """
        if not self.logged_in:
            logger.warning("Not logged in")
            print("❌ Not logged in. Please login first.")
            return None

        # Try incremental sync first if we have a last sync timestamp
        if self.last_sync_timestamp:
            logger.info("Checking for changes with incremental sync...")
            print("🔄 Checking for changes with incremental sync...")
            incremental_data = self.get_tasks_incremental(include_completed)

            if incremental_data is None:
                logger.warning("Incremental sync failed, falling back to full sync...")
                print("⚠️  Incremental sync failed, falling back to full sync...")
            elif self._has_meaningful_task_data(incremental_data):
                logger.info("Changes detected, performing full sync...")
                print("🔄 Changes detected! Performing full sync to get complete current state...")
                return self.get_tasks_full(include_completed)
            else:
                logger.info("No changes detected since last sync")
                print("✅ No changes detected since last sync")
                return incremental_data

        # Full sync (first time or fallback)
        logger.info("Performing full sync...")
        print("🔄 Performing full sync...")
        return self.get_tasks_full(include_completed)

    def get_tasks_incremental(self, include_completed: bool = False) -> Optional[Dict[str, Any]]:
        """
        Fetch only tasks updated since last sync using incremental sync.

        This method uses the updatedSince parameter to download only changes,
        significantly reducing server load and improving performance.

        Args:
            include_completed: Whether to include completed tasks

        Returns:
            Dict containing tasks data, or None if failed
        """
        if not self.logged_in:
            logger.warning("Not logged in")
            print("❌ Not logged in. Please login first.")
            return None

        if not self.last_sync_timestamp:
            logger.warning("No last sync timestamp available")
            print("❌ No last sync timestamp available. Use get_tasks_full() first.")
            return None

        try:
            sync_url = f"{self.base_url}/api/v14/me/bg_sync"
            params = {
                "updatedSince": self.last_sync_timestamp,
                "includeNonVisible": "false"
            }

            last_sync_time = datetime.fromtimestamp(self.last_sync_timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')
            logger.info("Requesting changes since: %s", last_sync_time)
            print(f"📊 Requesting changes since: {last_sync_time}")

            sync_response = self.session.get(
                sync_url,
                params=params,
                timeout=AuthConstants.REQUEST_TIMEOUT
            )
            sync_response.raise_for_status()

            sync_data = sync_response.json()
            task_id = sync_data.get('task_id')

            if not task_id:
                logger.error("Could not get sync task ID for incremental sync")
                print("❌ Could not get sync task ID for incremental sync")
                return None

            # Poll for sync results with exponential backoff
            poll_interval = SyncConstants.INITIAL_POLL_INTERVAL
            total_waited = 0.0
            result_response = None

            while total_waited < SyncConstants.MAX_POLL_WAIT_INCREMENTAL:
                time.sleep(poll_interval)
                total_waited += poll_interval

                result_url = f"{self.base_url}/me/bg_sync_result/{task_id}"
                result_response = self.session.get(
                    result_url,
                    timeout=AuthConstants.REQUEST_TIMEOUT
                )

                if result_response.status_code == 200:
                    break
                elif result_response.status_code == 202:
                    poll_interval = min(
                        poll_interval * SyncConstants.POLL_BACKOFF_MULTIPLIER,
                        SyncConstants.MAX_POLL_INTERVAL
                    )
                    continue
                else:
                    result_response.raise_for_status()

            if result_response is None or result_response.status_code != 200:
                logger.warning("Incremental sync operation timed out")
                print("⚠️  Incremental sync operation timed out")
                return None

            tasks_data = result_response.json()

            # Update last sync timestamp to current time
            self.last_sync_timestamp = int(time.time() * 1000)
            self._save_session()

            logger.info("Incremental sync completed successfully")
            print("✅ Incremental sync completed successfully")
            return tasks_data

        except requests.RequestException as e:
            logger.error("Error in incremental sync: %s", e)
            print(f"❌ Error in incremental sync: {str(e)}")
            return None

    def get_tasks_full(self, include_completed: bool = False) -> Optional[Dict[str, Any]]:
        """
        Fetch all tasks from Any.do using full sync.

        Downloads all tasks regardless of when they were last updated.
        Use this method for first-time sync or when incremental sync fails.

        Args:
            include_completed: Whether to include completed tasks

        Returns:
            Dict containing tasks data, or None if failed
        """
        if not self.logged_in:
            logger.warning("Not logged in")
            print("❌ Not logged in. Please login first.")
            return None

        # Rate limiting: prevent full syncs more than once per minute
        current_time = int(time.time() * 1000)
        if self.last_full_sync_timestamp:
            time_since_last = current_time - self.last_full_sync_timestamp
            if time_since_last < SyncConstants.FULL_SYNC_RATE_LIMIT_MS:
                seconds_left = (SyncConstants.FULL_SYNC_RATE_LIMIT_MS - time_since_last) / 1000
                logger.warning("Full sync rate limited. Wait %.1f seconds.", seconds_left)
                print(f"⏳ Full sync rate limited. Please wait {seconds_left:.1f} seconds before next full sync.")
                return None

        try:
            sync_url = f"{self.base_url}/api/v14/me/bg_sync"
            params = {
                "updatedSince": 0,
                "includeNonVisible": "false"
            }

            sync_response = self.session.get(
                sync_url,
                params=params,
                timeout=AuthConstants.REQUEST_TIMEOUT
            )
            sync_response.raise_for_status()

            sync_data = sync_response.json()
            task_id = sync_data.get('task_id')

            if not task_id:
                logger.error("Could not get sync task ID for full sync")
                print("❌ Could not get sync task ID for full sync")
                return None

            # Poll for sync results with exponential backoff
            poll_interval = SyncConstants.INITIAL_POLL_INTERVAL
            total_waited = 0.0
            result_response = None

            while total_waited < SyncConstants.MAX_POLL_WAIT_FULL_SYNC:
                time.sleep(poll_interval)
                total_waited += poll_interval

                result_url = f"{self.base_url}/me/bg_sync_result/{task_id}"
                result_response = self.session.get(
                    result_url,
                    timeout=AuthConstants.REQUEST_TIMEOUT
                )

                if result_response.status_code == 200:
                    break
                elif result_response.status_code == 202:
                    poll_interval = min(
                        poll_interval * SyncConstants.POLL_BACKOFF_MULTIPLIER,
                        SyncConstants.MAX_POLL_INTERVAL
                    )
                    continue
                else:
                    result_response.raise_for_status()

            if result_response is None or result_response.status_code != 200:
                logger.warning("Full sync operation timed out")
                print("⚠️  Full sync operation timed out")
                return None

            tasks_data = result_response.json()

            # Update last sync timestamp to current time
            self.last_sync_timestamp = int(time.time() * 1000)
            self.last_full_sync_timestamp = int(time.time() * 1000)
            self._save_session()

            logger.info("Full sync completed successfully")
            print("✅ Full sync completed successfully")
            return tasks_data

        except requests.RequestException as e:
            logger.error("Error in full sync: %s", e)
            print(f"❌ Error in full sync: {str(e)}")
            return None

    def _calculate_data_hash(self, data: Dict[str, Any]) -> str:
        """Calculate hash of task data for change detection."""
        data_str = json.dumps(data, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(data_str.encode()).hexdigest()

    def _has_meaningful_task_data(self, tasks_data: Dict[str, Any]) -> bool:
        """
        Check if tasks_data contains meaningful task information worth saving.

        Args:
            tasks_data: Raw task data from Any.do API

        Returns:
            True if data contains meaningful tasks, False if empty/minimal
        """
        if not tasks_data:
            return False

        if 'models' in tasks_data:
            models = tasks_data['models']

            if 'task' in models and models['task'].get('items'):
                return True

            if 'category' in models and models['category'].get('items'):
                categories = models['category']['items']
                if any(cat.get('name', '').strip() for cat in categories):
                    return True

            meaningful_models = ['user', 'label', 'attachment', 'sharedMember', 'space',
                               'board', 'section', 'customField', 'tag', 'card']
            for model_name in meaningful_models:
                if model_name in models and models[model_name].get('items'):
                    return True

        elif 'tasks' in tasks_data and tasks_data['tasks']:
            return True
        elif 'categories' in tasks_data and tasks_data['categories']:
            return True

        return False

    def save_tasks_to_file(self, tasks_data: Dict[str, Any]) -> Optional[str]:
        """
        Save tasks to timestamped JSON file with change detection.

        Args:
            tasks_data: Raw task data from Any.do API

        Returns:
            Path to saved file or None if no save needed
        """
        if not tasks_data:
            logger.warning("No tasks data to save")
            print("❌ No tasks data to save")
            return None

        if not self._has_meaningful_task_data(tasks_data):
            logger.info("No meaningful task data to save - skipping file creation")
            print("📋 No meaningful task data to save - skipping file creation")
            return None

        current_hash = self._calculate_data_hash(tasks_data)

        if self.last_data_hash == current_hash:
            logger.info("No changes detected since last export - skipping file creation")
            print("📋 No changes detected since last export - skipping file creation")
            return None

        os.makedirs("outputs/raw-json", exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M-%S")
        filename = f"{timestamp}_anydo-tasks.json"
        filepath = os.path.join("outputs/raw-json", filename)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(tasks_data, f, indent=2, ensure_ascii=False)

            self.last_data_hash = current_hash

            file_size = os.path.getsize(filepath)
            size_mb = file_size / (1024 * 1024)

            logger.info("Tasks exported to: %s (%.2f MB)", filepath, size_mb)
            print(f"📁 Tasks exported to: {filepath}")
            print(f"📊 File size: {size_mb:.2f} MB")

            self._save_markdown_from_json(tasks_data, timestamp)

            return filepath

        except OSError as e:
            logger.error("Error saving tasks: %s", e)
            print(f"❌ Error saving tasks: {e}")
            return None

    def _save_markdown_from_json(self, tasks_data: Dict[str, Any], timestamp: str) -> Optional[str]:
        """
        Generate markdown file directly from JSON data.
        Only creates new file if the human-useful data has changed.

        Args:
            tasks_data: Raw task data from Any.do API
            timestamp: Timestamp string for filename

        Returns:
            Path to saved markdown file or None if no save needed
        """
        try:
            pretty_data = self._extract_pretty_data(tasks_data, verbose=False)

            current_pretty_hash = self._calculate_data_hash(pretty_data)

            if self.last_pretty_hash == current_pretty_hash:
                logger.info("No changes in human-readable data - skipping markdown generation")
                print("📝 No changes in human-readable data - skipping markdown generation")
                return None

            markdown_file = self._save_markdown_tasks(pretty_data, timestamp, verbose=False)

            self.last_pretty_hash = current_pretty_hash

            return markdown_file

        except (KeyError, TypeError) as e:
            logger.error("Error saving markdown from JSON: %s", e)
            print(f"❌ Error saving markdown from JSON: {e}")
            return None

    def _save_markdown_tasks(self, pretty_data: Dict[str, Any], timestamp: str, verbose: bool = False) -> Optional[str]:
        """
        Generate markdown table from pretty task data.

        Args:
            pretty_data: Processed task data for markdown export
            timestamp: Timestamp string for filename
            verbose: Include all fields if True, clean output if False

        Returns:
            Path to saved markdown file or None if error
        """
        try:
            os.makedirs("outputs/markdown", exist_ok=True)

            suffix = "-verbose" if verbose else ""
            filename = f"{timestamp}_anydo-tasks{suffix}.md"
            filepath = os.path.join("outputs/markdown", filename)

            markdown_content = self._generate_markdown_content(pretty_data, verbose)

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(markdown_content)

            file_size = os.path.getsize(filepath)
            size_kb = file_size / 1024

            mode_text = "verbose " if verbose else ""
            logger.info("Markdown %stable exported to: %s (%.1f KB)", mode_text, filepath, size_kb)
            print(f"📝 Markdown {mode_text}table exported to: {filepath}")
            print(f"📊 Markdown size: {size_kb:.1f} KB")

            return filepath

        except OSError as e:
            logger.error("Error saving markdown tasks: %s", e)
            print(f"❌ Error saving markdown tasks: {e}")
            return None

    def _generate_markdown_content(self, pretty_data: Dict[str, Any], verbose: bool = False) -> str:
        """
        Generate markdown content from pretty task data.

        Args:
            pretty_data: Processed task data
            verbose: Include all fields if True, clean output if False

        Returns:
            Markdown content as string
        """
        lines = []

        mode = "Verbose" if verbose else "Clean"
        lines.append(f"# 📋 Any.do Tasks Export ({mode} Mode)")
        lines.append("")
        lines.append(f"*Generated: {pretty_data.get('export_info', {}).get('extracted_at', 'Unknown')}*")
        lines.append("")

        export_info = pretty_data.get('export_info', {})
        lines.append("## 📊 Export Summary")
        lines.append("")
        lines.append("| Metric | Count |")
        lines.append("|--------|-------|")
        lines.append(f"| 📋 Total Tasks | {export_info.get('total_tasks', 0)} |")
        lines.append(f"| ⏳ Pending Tasks | {export_info.get('pending_tasks', 0)} |")
        lines.append(f"| ✅ Completed Tasks | {export_info.get('completed_tasks', 0)} |")
        lines.append("")

        lists_info = pretty_data.get('lists', {})
        if lists_info:
            lines.append("## 📁 Lists Summary")
            lines.append("")
            lines.append("| List Name | Total | ⏳ Pending | ✅ Completed |")
            lines.append("|-----------|-------|---------|-----------|")

            for list_name, list_data in lists_info.items():
                total = list_data.get('task_count', 0)
                pending = list_data.get('pending_count', 0)
                completed = list_data.get('completed_count', 0)
                lines.append(f"| {list_name} | {total} | {pending} | {completed} |")
            lines.append("")

        tasks_data = pretty_data.get('tasks', {})
        if tasks_data:
            lines.append("## 📝 Tasks")
            lines.append("")

            all_tasks = []
            for list_name, tasks in tasks_data.items():
                for task in tasks:
                    task_with_list = task.copy()
                    task_with_list['list_name'] = list_name
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
                list_name = task.get('list_name', 'Unknown')

                created_full = task.get('created_date', 'N/A')
                if created_full != 'N/A' and ' ' in created_full:
                    created = created_full.split(' ')[0]
                else:
                    created = created_full

                due = task.get('due_date', '')

                title_cell = f"{status_emoji}{title}" if status_emoji else title

                note = task.get('note')
                if note and note.strip():
                    wrapped_note = self._wrap_text(note.strip(), markdown_safe=True)
                    note_formatted = wrapped_note.replace('<br>', '<br>&nbsp;&nbsp;&nbsp;')
                    title_cell += f" <br> &nbsp;&nbsp;&nbsp;<span style=\"color: #666; font-style: italic;\">{note_formatted}</span>"

                subtasks = task.get('subtasks', [])
                if subtasks:
                    subtask_lines = []
                    for subtask in subtasks:
                        subtask_status = self._get_status_emoji(subtask, verbose)
                        subtask_title = self._wrap_text(subtask.get('title', 'Untitled'), markdown_safe=True, truncate_long_lines=False)
                        if subtask_status:
                            subtask_lines.append(f"&nbsp;&nbsp;&nbsp;√&nbsp;&nbsp;{subtask_title}")
                        else:
                            subtask_lines.append(f"&nbsp;&nbsp;&nbsp;- {subtask_title}")

                    subtask_content = "<br>".join(subtask_lines)
                    title_cell += f"<br>{subtask_content}"

                if verbose:
                    priority = task.get('priority', 'normal')
                    priority_emoji = self._get_priority_emoji(priority)
                    assignee = task.get('assignee', '')
                    assignee_display = f"👤 {assignee}" if assignee else ''

                    lines.append(f"| {title_cell} | {list_name} | 📅 {created} | {due} | {priority_emoji} {priority} | {assignee_display} |")
                else:
                    due_display = f"⏰ {due}" if due else ''
                    lines.append(f"| {title_cell} | {list_name} | 📅 {created} | {due_display} |")

            lines.append("")

        return "\n".join(lines)

    def _sort_tasks_for_display(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sort tasks for display: pending with due dates first (by due date),
        then pending without due dates (newest first), then completed (newest first).
        """
        def parse_date(date_str: Optional[str]) -> Optional[datetime]:
            """Parse date string to datetime for sorting."""
            if not date_str:
                return None
            try:
                if ' ' in date_str:
                    return datetime.strptime(date_str, '%Y-%m-%d %H:%M')
                else:
                    return datetime.strptime(date_str, '%Y-%m-%d')
            except ValueError:
                return None

        def sort_key(task: Dict[str, Any]) -> tuple:
            """Generate sort key for task."""
            internal_status = task.get('_internal_status', 'pending')
            is_completed = internal_status == 'completed'

            created_date = parse_date(task.get('created_date', ''))
            due_date = parse_date(task.get('due_date', ''))

            if is_completed:
                created_timestamp = created_date.timestamp() if created_date else 0
                return (1, -created_timestamp)
            else:
                if due_date:
                    created_timestamp = created_date.timestamp() if created_date else 0
                    return (0, due_date.timestamp(), -created_timestamp)
                else:
                    created_timestamp = created_date.timestamp() if created_date else 0
                    return (0, float('inf'), -created_timestamp)

        return sorted(tasks, key=sort_key)

    def _get_status_emoji(self, task: Dict[str, Any], verbose: bool = False) -> str:
        """Get status emoji for a task."""
        if verbose:
            status = task.get('status', 'pending')
            return "√&nbsp;&nbsp;" if status == 'completed' else ""
        else:
            internal_status = task.get('_internal_status')
            if internal_status:
                return "√&nbsp;&nbsp;" if internal_status == 'completed' else ""
            return ""

    def _get_priority_emoji(self, priority: str) -> str:
        """Get priority emoji."""
        priority_lower = priority.lower()
        if priority_lower == 'high':
            return "🔴"
        elif priority_lower == 'medium':
            return "🟡"
        else:
            return "🟢"

    def _format_task_title(self, task: Dict[str, Any]) -> str:
        """Format task title with markdown-safe text truncation."""
        title = task.get('title', 'Untitled Task')
        return self._wrap_text(title, markdown_safe=True, truncate_long_lines=True)

    def _format_timestamp(self, timestamp: int, include_seconds: bool = True) -> str:
        """
        Format a timestamp to a human-readable string.

        Args:
            timestamp: Unix timestamp in milliseconds
            include_seconds: Whether to include seconds in the output

        Returns:
            Formatted date string
        """
        try:
            timestamp_seconds = int(timestamp) / 1000
            dt = datetime.fromtimestamp(timestamp_seconds)

            if include_seconds:
                return dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                return dt.strftime('%Y-%m-%d %H:%M')
        except (ValueError, TypeError, OSError):
            return 'Invalid date'

    def _extract_pretty_data(self, tasks_data: Dict[str, Any], verbose: bool = False) -> Dict[str, Any]:
        """
        Extract human-readable task information from raw API data.

        Args:
            tasks_data: Raw task data from Any.do API
            verbose: Include all fields if True, clean output if False

        Returns:
            Dictionary with clean task data for markdown export
        """
        try:
            export_info: ExportInfo = {
                'extracted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'total_tasks': 0,
                'pending_tasks': 0,
                'completed_tasks': 0
            }

            lists_info: Dict[str, ListInfo] = {}
            if 'models' in tasks_data and 'category' in tasks_data['models']:
                for list_item in tasks_data['models']['category']['items']:
                    list_name = list_item.get('name', 'Unknown List')
                    list_info: ListInfo = {
                        'task_count': 0,
                        'pending_count': 0,
                        'completed_count': 0
                    }

                    if verbose:
                        list_info['color'] = list_item.get('color')
                        list_info['is_default'] = list_item.get('isDefault', False)

                    lists_info[list_name] = list_info

            all_tasks: List[TaskInfo] = []
            tasks_by_id: Dict[str, TaskInfo] = {}

            if 'models' in tasks_data and 'task' in tasks_data['models']:
                for task in tasks_data['models']['task']['items']:
                    task_id = task.get('globalTaskId')
                    parent_id = task.get('parentGlobalTaskId')

                    task_info: TaskInfo = {
                        'title': task.get('title', 'Untitled Task'),
                        'id': task_id,
                        'parent_id': parent_id
                    }

                    if task.get('creationDate'):
                        if verbose:
                            task_info['created_date'] = self._format_timestamp(task['creationDate'], include_seconds=True)
                        else:
                            task_info['created_date'] = self._format_timestamp(task['creationDate'], include_seconds=False)

                    if task.get('lastUpdateDate'):
                        if verbose:
                            task_info['last_update'] = self._format_timestamp(task['lastUpdateDate'], include_seconds=True)
                        else:
                            task_info['last_update'] = self._format_timestamp(task['lastUpdateDate'], include_seconds=False)

                    if task.get('dueDate'):
                        if verbose:
                            task_info['due_date'] = self._format_timestamp(task['dueDate'], include_seconds=True)
                        else:
                            task_info['due_date'] = self._format_timestamp(task['dueDate'], include_seconds=False)

                    list_name = 'Unknown List'
                    if task.get('categoryId') and 'models' in tasks_data and 'category' in tasks_data['models']:
                        for list_item in tasks_data['models']['category']['items']:
                            if list_item.get('id') == task['categoryId']:
                                list_name = list_item.get('name', 'Unknown List')
                                break
                    task_info['list_name'] = list_name

                    note = task.get('note')
                    if note and note.strip():
                        task_info['note'] = note.strip()

                    if task.get('labels'):
                        task_info['tags'] = task['labels']

                    task_info['_internal_status'] = 'completed' if task.get('status') == 'CHECKED' else 'pending'

                    if verbose:
                        task_info['status'] = 'completed' if task.get('status') == 'CHECKED' else 'pending'
                        task_info['priority'] = task.get('priority', 'Normal').lower()
                        task_info['list_color'] = None
                        task_info['assignee'] = task.get('assignedTo')
                        task_info['repeating'] = task.get('repeatingMethod', 'TASK_REPEAT_OFF')

                        if task.get('categoryId') and 'models' in tasks_data and 'category' in tasks_data['models']:
                            for list_item in tasks_data['models']['category']['items']:
                                if list_item.get('id') == task['categoryId']:
                                    task_info['list_color'] = list_item.get('color')
                                    break

                    is_completed = task.get('status') == 'CHECKED'
                    export_info['total_tasks'] += 1
                    if is_completed:
                        export_info['completed_tasks'] += 1
                    else:
                        export_info['pending_tasks'] += 1

                    if list_name in lists_info:
                        lists_info[list_name]['task_count'] += 1
                        if is_completed:
                            lists_info[list_name]['completed_count'] += 1
                        else:
                            lists_info[list_name]['pending_count'] += 1

                    tasks_by_id[task_id] = task_info
                    all_tasks.append(task_info)

            parent_tasks: List[TaskInfo] = []
            subtasks_by_parent: Dict[str, List[TaskInfo]] = {}

            for task in all_tasks:
                if task.get('parent_id') is None:
                    parent_tasks.append(task)
                else:
                    parent_id = task['parent_id']
                    if parent_id not in subtasks_by_parent:
                        subtasks_by_parent[parent_id] = []
                    subtasks_by_parent[parent_id].append(task)

            for parent_task in parent_tasks:
                parent_id = parent_task.get('id')
                if parent_id and parent_id in subtasks_by_parent:
                    subtasks = sorted(subtasks_by_parent[parent_id], key=lambda x: x.get('title', ''))

                    for subtask in subtasks:
                        subtask.pop('id', None)
                        subtask.pop('parent_id', None)
                    parent_task['subtasks'] = subtasks

            for task in parent_tasks:
                task.pop('id', None)
                task.pop('parent_id', None)

            tasks_by_list: Dict[str, List[TaskInfo]] = {}
            for task in parent_tasks:
                list_name = task.get('list_name', 'Unknown List')
                if list_name not in tasks_by_list:
                    tasks_by_list[list_name] = []
                tasks_by_list[list_name].append(task)

            for list_name in tasks_by_list:
                tasks_by_list[list_name].sort(key=lambda x: x.get('title', ''))

            result = {
                'export_info': export_info,
                'lists': lists_info,
                'tasks': tasks_by_list
            }

            return result

        except (KeyError, TypeError) as e:
            logger.warning("Error extracting pretty data: %s", e)
            print(f"⚠️  Error extracting pretty data: {e}")
            return {
                'export_info': {'error': str(e)},
                'lists': {},
                'tasks': {}
            }

    def get_simple_tasks(self, tasks_data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Get a simplified list of tasks with just the essential information.

        Args:
            tasks_data: Optional pre-fetched tasks data. If None, will fetch tasks.

        Returns:
            List of task dictionaries with title, completed status, due date, etc.
        """
        if tasks_data is None:
            tasks_data = self.get_tasks()
        if not tasks_data:
            return []

        simple_tasks: List[Dict[str, Any]] = []

        if 'models' in tasks_data and 'task' in tasks_data['models']:
            task_items = tasks_data['models']['task'].get('items', [])
            for task in task_items:
                simple_task = {
                    'title': task.get('title', 'Untitled'),
                    'completed': task.get('status') == 'CHECKED',
                    'due_date': task.get('dueDate'),
                    'priority': task.get('priority', 'NORMAL'),
                    'list_id': task.get('categoryId'),
                    'id': task.get('id'),
                    'note': task.get('note'),
                    'creation_date': task.get('creationDate'),
                    'last_update': task.get('lastUpdateDate')
                }
                simple_tasks.append(simple_task)

        elif 'tasks' in tasks_data:
            for task in tasks_data['tasks']:
                simple_task = {
                    'title': task.get('title', 'Untitled'),
                    'completed': task.get('status') == 'DONE',
                    'due_date': task.get('dueDate'),
                    'priority': task.get('priority', 'NORMAL'),
                    'list_id': task.get('categoryId'),
                    'id': task.get('id')
                }
                simple_tasks.append(simple_task)

        return simple_tasks

    def get_lists(self, tasks_data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Get all task lists/categories.

        Args:
            tasks_data: Optional pre-fetched tasks data. If None, will fetch tasks.

        Returns:
            List of list dictionaries
        """
        if tasks_data is None:
            tasks_data = self.get_tasks()
        if not tasks_data:
            return []

        lists: List[Dict[str, Any]] = []

        if 'models' in tasks_data and 'category' in tasks_data['models']:
            category_items = tasks_data['models']['category'].get('items', [])
            for category in category_items:
                list_info = {
                    'id': category.get('id'),
                    'name': category.get('name', 'Untitled List'),
                    'color': category.get('color'),
                    'is_default': category.get('isDefault', False),
                    'position': category.get('position'),
                    'is_deleted': category.get('isDeleted', False)
                }
                if not list_info['is_deleted']:
                    lists.append(list_info)

        elif 'categories' in tasks_data:
            for category in tasks_data['categories']:
                list_info = {
                    'id': category.get('id'),
                    'name': category.get('name', 'Untitled List'),
                    'color': category.get('color'),
                    'is_default': category.get('isDefault', False)
                }
                lists.append(list_info)

        return lists

    def print_tasks_summary(self, tasks_data: Optional[Dict[str, Any]] = None) -> None:
        """Print a nice summary of all tasks."""
        tasks = self.get_simple_tasks(tasks_data)
        lists = self.get_lists(tasks_data)

        if not tasks:
            print("No tasks found.")
            return

        list_names = {lst['id']: lst['name'] for lst in lists}

        print(f"\n=== Your Any.do Tasks ({len(tasks)} total) ===")

        pending_tasks = [t for t in tasks if not t['completed']]
        completed_tasks = [t for t in tasks if t['completed']]

        print(f"\n📋 Pending Tasks ({len(pending_tasks)}):")
        for task in pending_tasks:
            list_name = list_names.get(task['list_id'], 'Unknown List')
            due_info = f" (Due: {task['due_date']})" if task['due_date'] else ""
            priority_icon = "🔴" if task['priority'] == 'HIGH' else "🟡" if task['priority'] == 'MEDIUM' else "⚪"
            print(f"  {priority_icon} {task['title']} [{list_name}]{due_info}")

        if completed_tasks:
            print(f"\n✅ Completed Tasks ({len(completed_tasks)}):")
            for task in completed_tasks[:5]:
                list_name = list_names.get(task['list_id'], 'Unknown List')
                print(f"  ✓ {task['title']} [{list_name}]")

            if len(completed_tasks) > 5:
                print(f"  ... and {len(completed_tasks) - 5} more completed tasks")

    def _wrap_text(self, text: str, width: Optional[int] = None, markdown_safe: bool = False, truncate_long_lines: bool = False) -> str:
        """
        Wrap text to specified width, preserving line breaks.

        Args:
            text: Text to wrap
            width: Width to wrap to (defaults to self.text_wrap_width)
            markdown_safe: If True, use <br> instead of \\n for line breaks (for markdown tables)
            truncate_long_lines: If True, truncate very long lines instead of wrapping them

        Returns:
            Wrapped text
        """
        if not text:
            return text

        wrap_width = width or (100 if markdown_safe else self.text_wrap_width)

        if markdown_safe and truncate_long_lines:
            lines = text.split('\n')
            processed_lines = []

            for line in lines:
                if len(line) <= wrap_width:
                    processed_lines.append(line)
                else:
                    truncated = line[:wrap_width-3] + "..."
                    processed_lines.append(truncated)

            return '<br>'.join(processed_lines)
        elif markdown_safe:
            lines = text.split('\n')
            all_wrapped_lines = []

            for line in lines:
                if len(line) <= wrap_width:
                    all_wrapped_lines.append(line)
                else:
                    wrapped_lines = textwrap.wrap(line, width=wrap_width,
                                                break_long_words=False,
                                                break_on_hyphens=False)
                    all_wrapped_lines.extend(wrapped_lines)

            return '<br>'.join(all_wrapped_lines)
        else:
            lines = text.split('\n')
            all_wrapped_lines = []

            for line in lines:
                if len(line) <= wrap_width:
                    all_wrapped_lines.append(line)
                else:
                    wrapped_lines = textwrap.wrap(line, width=wrap_width,
                                                break_long_words=False,
                                                break_on_hyphens=False)
                    all_wrapped_lines.extend(wrapped_lines)

            return '\n'.join(all_wrapped_lines)
