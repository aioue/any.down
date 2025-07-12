import requests
import json
import time
import os
import hashlib
import textwrap
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any


class AnyDoClient:
    """
    A Python client for the Any.do API.
    
    This client handles authentication, session persistence, and provides methods 
    to interact with your Any.do tasks and lists.
    """
    
    def __init__(self, session_file: str = "session.json", text_wrap_width: int = 80):
        self.session = requests.Session()
        self.base_url = "https://sm-prod4.any.do"
        self.logged_in = False
        self.user_info = None
        self.session_file = session_file
        self.last_data_hash = None
        self.last_pretty_hash = None  # Track pretty data changes separately
        self.text_wrap_width = text_wrap_width  # Configure text wrapping width
        self.last_sync_timestamp = None  # Track last sync timestamp for incremental updates
        self.client_id = str(uuid.uuid4())  # Generate unique client ID per session
        
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
            'X-Anydo-Platform': 'web',
            'X-Anydo-Version': '5.0.97',
            'X-Platform': '3'
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
                # Restore hash tracking for change detection
                self.last_data_hash = session_data.get('last_data_hash')
                self.last_pretty_hash = session_data.get('last_pretty_hash')
                self.last_sync_timestamp = session_data.get('last_sync_timestamp')
                print(f"📱 Loaded existing session for {self.user_info.get('email', 'unknown user') if self.user_info else 'unknown user'}")
                
                # Test if session is still valid
                if self._test_session():
                    self.logged_in = True
                    print("✅ Session is still valid")
                    return True
                else:
                    print("⚠️  Session expired, will need to login again")
                    self._clear_session()
                    return False
                    
            except Exception as e:
                print(f"⚠️  Error loading session: {e}")
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
                'last_sync_timestamp': self.last_sync_timestamp
            }
            
            with open(self.session_file, 'w') as f:
                json.dump(session_data, f, indent=2)
            
            print("💾 Session saved successfully")
            
        except Exception as e:
            print(f"⚠️  Error saving session: {e}")
    
    def _clear_session(self) -> None:
        """Clear session data."""
        self.session.cookies.clear()
        self.user_info = None
        self.logged_in = False
        if os.path.exists(self.session_file):
            try:
                os.remove(self.session_file)
            except:
                pass
    
    def _test_session(self) -> bool:
        """Test if current session is still valid."""
        try:
            user_url = f"{self.base_url}/me"
            response = self.session.get(user_url, timeout=10)
            return response.status_code == 200
        except:
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
            print("✅ Already logged in with valid session")
            return True
            
        try:
            # Store credentials for the login process
            self._temp_email = email
            self._temp_password = password
            
            # Step 1: Check if email exists in system
            print("🔐 Checking email...")
            check_email_url = f"{self.base_url}/check_email"
            check_email_data = {"email": email}
            
            # Add delay to prevent rate limiting
            time.sleep(2)
            response = self.session.post(check_email_url, json=check_email_data)
            
            if response.status_code == 200:
                email_data = response.json()
                if not email_data.get('user_exists', False):
                    print("❌ Email not found in system")
                    return False
                print("✅ Email found in system")
            else:
                print(f"⚠️  Email check failed: {response.status_code}, continuing...")
            
            # Step 2: Attempt 2FA login flow (this is the standard flow for most accounts)
            print("🔐 Attempting 2FA login flow...")
            if self._trigger_2fa_email():
                return self._handle_2fa_interactive()
            else:
                print("❌ Failed to trigger 2FA email")
                return False
                
        except Exception as e:
            print(f"❌ Login error: {str(e)}")
            return False
    
    def _handle_2fa_interactive(self) -> bool:
        """Handle 2FA verification with interactive prompts."""
        
        # First, trigger the 2FA email to be sent
        if not self._trigger_2fa_email():
            print("❌ Failed to trigger 2FA email")
            return False
        
        print("\n🔐 2FA verification required. Check your email for the code.")
        
        for attempt in range(3):
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
                    # Clean up temporary credentials
                    if hasattr(self, '_temp_email'):
                        delattr(self, '_temp_email')
                    if hasattr(self, '_temp_password'):
                        delattr(self, '_temp_password')
                    return True
                else:
                    remaining = 2 - attempt
                    if remaining > 0:
                        print(f"Invalid code. {remaining} attempts left.")
                    
            except KeyboardInterrupt:
                print("\nCancelled.")
                # Clean up temporary credentials
                if hasattr(self, '_temp_email'):
                    delattr(self, '_temp_email')
                if hasattr(self, '_temp_password'):
                    delattr(self, '_temp_password')
                return False
        
        print("Too many failed attempts.")
        # Clean up temporary credentials
        if hasattr(self, '_temp_email'):
            delattr(self, '_temp_email')
        if hasattr(self, '_temp_password'):
            delattr(self, '_temp_password')
        return False

    def _trigger_2fa_email(self) -> bool:
        """Trigger 2FA email to be sent using the /login-2fa endpoint."""
        try:
            if not hasattr(self, '_temp_email') or not hasattr(self, '_temp_password'):
                print("❌ Missing credentials for 2FA email trigger")
                return False
            
            print("📧 Triggering 2FA email...")
            
            # Trigger 2FA email (this is the crucial step!)
            login_2fa_url = f"{self.base_url}/login-2fa"
            login_2fa_data = {
                "platform": "web",
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
            
            # Add delay before triggering 2FA email
            time.sleep(2)
            response = self.session.post(login_2fa_url, json=login_2fa_data)
            if response.status_code == 200:
                print("✅ 2FA email triggered successfully!")
                return True
            else:
                print(f"⚠️  2FA email trigger returned {response.status_code}, but continuing...")
                return True  # Continue even if trigger fails, maybe email was already sent
                
        except Exception as e:
            print(f"❌ Error triggering 2FA email: {e}")
            return False

    def _verify_2fa_code(self, code: str) -> bool:
        """Verify 2FA code with Any.do servers."""
        try:
            if not hasattr(self, '_temp_email') or not hasattr(self, '_temp_password'):
                print("❌ Missing credentials for 2FA verification")
                return False
            
            # Use the exact same endpoint and parameters as the browser
            verify_url = f"{self.base_url}/login-2fa-code"
            verify_data = {
                "platform": "web",
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
            
            # Add delay before 2FA verification
            time.sleep(1)
            response = self.session.post(verify_url, json=verify_data)
            
            if response.status_code == 200:
                try:
                    # Handle potential compression issues by forcing response parsing
                    response_data = response.json()
                    
                    if 'auth_token' in response_data:
                        # Store auth token and set it in session headers
                        self.auth_token = response_data['auth_token']
                        self.session.headers['X-Anydo-Auth'] = self.auth_token
                        print("✅ 2FA verification successful!")
                        return True
                    else:
                        print(f"❌ 2FA verification failed - no auth token in response")
                        return False
                except Exception as e:
                    print(f"❌ Error parsing 2FA response: {e}")
                    # Try to get the auth token from response headers as fallback
                    auth_token = response.headers.get('X-Anydo-Auth')
                    if auth_token:
                        print("✅ Found auth token in response headers!")
                        self.auth_token = auth_token
                        self.session.headers['X-Anydo-Auth'] = auth_token
                        print("✅ 2FA verification successful!")
                        return True
                    else:
                        print("❌ No auth token found in headers either")
                        return False
            else:
                print(f"❌ 2FA verification failed with status: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Error verifying 2FA code: {e}")
            return False

    def _try_alternative_login(self, email: str, password: str) -> bool:
        """Try alternative login methods."""
        try:
            # Try different login endpoints that Any.do might use
            alternative_endpoints = [
                f"{self.base_url}/j_spring_security_check",
                f"{self.base_url}/api/v14/login",
                f"{self.base_url}/auth/login"
            ]
            
            for endpoint in alternative_endpoints:
                print(f"🔄 Trying alternative endpoint: {endpoint}")
                
                # Try form data instead of JSON
                login_data = {
                    "j_username": email,
                    "j_password": password,
                    "email": email,
                    "password": password
                }
                
                response = self.session.post(endpoint, data=login_data)
                print(f"Alternative login response: {response.status_code}")
                
                if response.status_code == 200:
                    # For Spring Security endpoints, check for authentication success
                    if 'j_spring_security_check' in endpoint:
                        # Spring Security typically redirects on success or sets cookies
                        # Check if we have authentication cookies
                        auth_cookies = [cookie for cookie in self.session.cookies 
                                      if any(keyword in cookie.name.lower() 
                                           for keyword in ['spring', 'session', 'auth', 'remember'])]
                        
                        if auth_cookies:
                            print(f"✅ Spring Security login successful (got {len(auth_cookies)} auth cookies)")
                            self.logged_in = True
                            # Try to get user info to confirm login
                            if self._get_user_info():
                                self._save_session()
                                return True
                            else:
                                # Even if user info fails, we might still be logged in
                                # Try to fetch tasks to confirm
                                print("User info failed, testing task access...")
                                try:
                                    test_tasks = self.get_tasks()
                                    if test_tasks is not None:
                                        print("✅ Task access successful - login confirmed!")
                                        self._save_session()
                                        return True
                                except:
                                    pass
                        
                        # Check if the response indicates 2FA is needed
                        if '2fa' in response.text.lower() or 'two-factor' in response.text.lower():
                            return self._handle_2fa_interactive()
                    
                    # Check if we got a proper JSON response
                    try:
                        response_data = response.json()
                        if response_data.get('requires2FA', False) or response_data.get('twoFactorRequired', False):
                            return self._handle_2fa_interactive()
                        else:
                            self.logged_in = True
                            self._get_user_info()
                            self._save_session()
                            return True
                    except:
                        # If response is not JSON, check if we have session cookies
                        if any('session' in cookie.name.lower() or 'auth' in cookie.name.lower() for cookie in self.session.cookies):
                            print("✅ Login appears successful (got session cookies)")
                            self.logged_in = True
                            self._get_user_info()
                            self._save_session()
                            return True
                elif response.status_code == 302:
                    # Redirect might indicate successful login
                    print("Got redirect, checking if login was successful...")
                    self.logged_in = True
                    if self._get_user_info():
                        self._save_session()
                        return True
            
            print("❌ All alternative login methods failed")
            return False
            
        except Exception as e:
            print(f"❌ Alternative login error: {str(e)}")
            return False

    def _handle_2fa(self, code: str) -> bool:
        """Handle 2FA verification - deprecated, use _handle_2fa_interactive instead."""
        return self._verify_2fa_code(code)
    
    def _get_user_info(self) -> bool:
        """Get user information after login."""
        try:
            user_url = f"{self.base_url}/me"
            response = self.session.get(user_url)
            
            if response.status_code == 200:
                self.user_info = response.json()
                print(f"✅ Logged in as: {self.user_info.get('email', 'Unknown')}")
                
                # Update timezone to match browser behavior
                self._update_timezone()
                
                return True
            else:
                print(f"⚠️  Failed to get user info: {response.status_code}")
                return False
            
        except Exception as e:
            print(f"❌ Error getting user info: {str(e)}")
            return False
    
    def _update_timezone(self) -> None:
        """Update user timezone to match browser handshake."""
        try:
            # Get local timezone (simplified approach)
            import time
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
            
            response = self.session.put(update_url, json=update_data)
            if response.status_code == 200:
                print(f"✅ Timezone updated to: {timezone_to_send}")
            else:
                print(f"⚠️  Timezone update failed: {response.status_code}")
                
        except Exception as e:
            print(f"⚠️  Error updating timezone: {str(e)}")
            # Don't fail login for timezone update issues
    
    def get_tasks(self, include_completed: bool = False) -> Optional[Dict[str, Any]]:
        """
        Fetch tasks from Any.do using smart sync strategy.
        
        Uses incremental sync if possible (when last sync timestamp exists),
        otherwise falls back to full sync. This reduces server load significantly.
        
        Args:
            include_completed: Whether to include completed tasks
            
        Returns:
            Dict containing tasks data, or None if failed
        """
        if not self.logged_in:
            print("❌ Not logged in. Please login first.")
            return None
            
        # Try incremental sync first if we have a last sync timestamp
        if self.last_sync_timestamp:
            print("🔄 Attempting incremental sync...")
            tasks_data = self.get_tasks_incremental(include_completed)
            if tasks_data:
                return tasks_data
            
            # If incremental fails, fall back to full sync
            print("⚠️  Incremental sync failed, falling back to full sync...")
        
        # Full sync (first time or fallback)
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
            print("❌ Not logged in. Please login first.")
            return None
            
        if not self.last_sync_timestamp:
            print("❌ No last sync timestamp available. Use get_tasks_full() first.")
            return None
            
        try:
            # Step 1: Start background sync with updatedSince parameter
            sync_url = f"{self.base_url}/api/v14/me/bg_sync"
            params = {
                "updatedSince": self.last_sync_timestamp,
                "includeNonVisible": "false"
            }
            
            print(f"📊 Requesting changes since: {datetime.fromtimestamp(self.last_sync_timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')}")
            
            sync_response = self.session.get(sync_url, params=params)
            sync_response.raise_for_status()
            
            sync_data = sync_response.json()
            task_id = sync_data.get('task_id')
            
            if not task_id:
                print("❌ Could not get sync task ID for incremental sync")
                return None
            
            # Step 2: Wait a moment for sync to complete
            time.sleep(1)
            
            # Step 3: Get sync results (contains only updated tasks)
            result_url = f"{self.base_url}/me/bg_sync_result/{task_id}"
            result_response = self.session.get(result_url)
            result_response.raise_for_status()
            
            tasks_data = result_response.json()
            
            # Update last sync timestamp to current time
            self.last_sync_timestamp = int(time.time() * 1000)
            self._save_session()  # Save updated timestamp
            
            print("✅ Incremental sync completed successfully")
            return tasks_data
            
        except Exception as e:
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
            print("❌ Not logged in. Please login first.")
            return None
            
        try:
            # Step 1: Start background sync with updatedSince=0 (full sync)
            sync_url = f"{self.base_url}/api/v14/me/bg_sync"
            params = {
                "updatedSince": 0,
                "includeNonVisible": "false"
            }
            
            sync_response = self.session.get(sync_url, params=params)
            sync_response.raise_for_status()
            
            sync_data = sync_response.json()
            task_id = sync_data.get('task_id')
            
            if not task_id:
                print("❌ Could not get sync task ID for full sync")
                return None
            
            # Step 2: Wait a moment for sync to complete
            time.sleep(1)
            
            # Step 3: Get sync results (contains all tasks)
            result_url = f"{self.base_url}/me/bg_sync_result/{task_id}"
            result_response = self.session.get(result_url)
            result_response.raise_for_status()
            
            tasks_data = result_response.json()
            
            # Update last sync timestamp to current time
            self.last_sync_timestamp = int(time.time() * 1000)
            self._save_session()  # Save updated timestamp
            
            print("✅ Full sync completed successfully")
            return tasks_data
            
        except Exception as e:
            print(f"❌ Error in full sync: {str(e)}")
            return None

    def _calculate_data_hash(self, data: Dict[str, Any]) -> str:
        """Calculate hash of task data for change detection."""
        # Create a normalized string representation of the data
        data_str = json.dumps(data, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(data_str.encode()).hexdigest()

    def save_tasks_to_file(self, tasks_data: Dict, force: bool = False) -> Optional[str]:
        """
        Save tasks to timestamped JSON file with change detection.
        
        Args:
            tasks_data: Raw task data from Any.do API
            force: Force save even if no changes detected
            
        Returns:
            Path to saved file or None if no save needed
        """
        if not tasks_data:
            print("❌ No tasks data to save")
            return None
            
        # Calculate hash for change detection
        current_hash = self._calculate_data_hash(tasks_data)
        
        # Check if data has changed (unless forced)
        if not force and self.last_data_hash == current_hash:
            print("📋 No changes detected since last export - skipping file creation")
            return None
            
        # Create outputs directory
        os.makedirs("outputs/raw-json", exist_ok=True)
        
        # Generate timestamped filename
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M-%S")
        filename = f"{timestamp}_anydo-tasks.json"
        filepath = os.path.join("outputs/raw-json", filename)
        
        # Save JSON file
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(tasks_data, f, indent=2, ensure_ascii=False)
            
            # Update hash after successful save
            self.last_data_hash = current_hash
            
            # Get file size for display
            file_size = os.path.getsize(filepath)
            size_mb = file_size / (1024 * 1024)
            
            print(f"📁 Tasks exported to: {filepath}")
            print(f"📊 File size: {size_mb:.2f} MB")
            
            # Generate markdown file
            self._save_markdown_from_json(tasks_data, timestamp)
            
            return filepath
            
        except Exception as e:
            print(f"❌ Error saving tasks: {e}")
            return None

    def _save_markdown_from_json(self, tasks_data: Dict, timestamp: str) -> Optional[str]:
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
            # Extract human-readable task information
            pretty_data = self._extract_pretty_data(tasks_data, verbose=False)
            
            # Calculate hash for pretty data change detection
            current_pretty_hash = self._calculate_data_hash(pretty_data)
            
            # Check if pretty data has changed
            if self.last_pretty_hash == current_pretty_hash:
                print("📝 No changes in human-readable data - skipping markdown generation")
                return None
            
            # Generate markdown file
            markdown_file = self._save_markdown_tasks(pretty_data, timestamp, verbose=False)
            
            # Update hash after successful save
            self.last_pretty_hash = current_pretty_hash
            
            return markdown_file
            
        except Exception as e:
            print(f"❌ Error saving markdown from JSON: {e}")
            return None

    def _save_markdown_tasks(self, pretty_data: Dict, timestamp: str, verbose: bool = False) -> Optional[str]:
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
            # Create markdown directory
            os.makedirs("outputs/markdown", exist_ok=True)
            
            # Generate filename
            suffix = "-verbose" if verbose else ""
            filename = f"{timestamp}_anydo-tasks{suffix}.md"
            filepath = os.path.join("outputs/markdown", filename)
            
            # Generate markdown content
            markdown_content = self._generate_markdown_content(pretty_data, verbose)
            
            # Save markdown file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(markdown_content)
            
            # Get file size for display
            file_size = os.path.getsize(filepath)
            size_kb = file_size / 1024
            
            mode_text = "verbose " if verbose else ""
            print(f"📝 Markdown {mode_text}table exported to: {filepath}")
            print(f"📊 Markdown size: {size_kb:.1f} KB")
            
            return filepath
            
        except Exception as e:
            print(f"❌ Error saving markdown tasks: {e}")
            return None

    def _generate_markdown_content(self, pretty_data: Dict, verbose: bool = False) -> str:
        """
        Generate markdown content from pretty task data.
        
        Args:
            pretty_data: Processed task data
            verbose: Include all fields if True, clean output if False
            
        Returns:
            Markdown content as string
        """
        lines = []
        
        # Header
        mode = "Verbose" if verbose else "Clean"
        lines.append(f"# 📋 Any.do Tasks Export ({mode} Mode)")
        lines.append("")
        lines.append(f"*Generated: {pretty_data.get('export_info', {}).get('extracted_at', 'Unknown')}*")
        lines.append("")
        
        # Export info table
        export_info = pretty_data.get('export_info', {})
        lines.append("## 📊 Export Summary")
        lines.append("")
        lines.append("| Metric | Count |")
        lines.append("|--------|-------|")
        lines.append(f"| 📋 Total Tasks | {export_info.get('total_tasks', 0)} |")
        lines.append(f"| ⏳ Pending Tasks | {export_info.get('pending_tasks', 0)} |")
        lines.append(f"| ✅ Completed Tasks | {export_info.get('completed_tasks', 0)} |")
        lines.append("")
        
        # Lists summary table
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
        
        # Single tasks table with all tasks
        tasks_data = pretty_data.get('tasks', {})
        if tasks_data:
            lines.append("## 📝 Tasks")
            lines.append("")
            
            # Collect all tasks from all lists
            all_tasks = []
            for list_name, tasks in tasks_data.items():
                for task in tasks:
                    # Add list name to each task
                    task_with_list = task.copy()
                    task_with_list['list_name'] = list_name
                    all_tasks.append(task_with_list)
            
            # Sort all tasks together
            sorted_tasks = self._sort_tasks_for_display(all_tasks)
            
            # Single table headers with List column
            if verbose:
                lines.append("| Title | List | Created | Due | Priority | Assignee |")
                lines.append("|-------|------|----------------------|---------------------|----------|----------|")
            else:
                lines.append("| Title | List | Created | Due |")
                lines.append("|-------|------|----------------------|---------------------|")
            
            # Process all tasks in one table
            for task in sorted_tasks:
                status_emoji = self._get_status_emoji(task, verbose)
                title = self._format_task_title(task)
                list_name = task.get('list_name', 'Unknown')
                
                # Format created date without time (just date)
                created_full = task.get('created_date', 'N/A')
                if created_full != 'N/A' and ' ' in created_full:
                    created = created_full.split(' ')[0]  # Take only the date part
                else:
                    created = created_full
                
                due = task.get('due_date', '')
                
                # Build title with status emoji and note
                title_cell = f"{status_emoji}{title}" if status_emoji else title
                
                # Add note if present - move to same cell as title
                note = task.get('note')
                if note and note.strip():
                    # Wrap note text using markdown-safe wrapping and add indentation to each line
                    wrapped_note = self._wrap_text(note.strip(), markdown_safe=True)
                    note_formatted = wrapped_note.replace('<br>', '<br>&nbsp;&nbsp;&nbsp;')
                    title_cell += f" <br> &nbsp;&nbsp;&nbsp;<span style=\"color: #666; font-style: italic;\">{note_formatted}</span>"
                
                # Add subtasks if present - include in the title cell
                subtasks = task.get('subtasks', [])
                if subtasks:
                    subtask_lines = []
                    for subtask in subtasks:
                        subtask_status = self._get_status_emoji(subtask, verbose)
                        subtask_title = self._wrap_text(subtask.get('title', 'Untitled'), markdown_safe=True, truncate_long_lines=False)
                        if subtask_status:  # Completed
                            subtask_lines.append(f"&nbsp;&nbsp;&nbsp;√&nbsp;&nbsp;{subtask_title}")
                        else:  # Pending
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

    def _sort_tasks_for_display(self, tasks: list) -> list:
        """
        Sort tasks for display: pending with due dates first (by due date), 
        then pending without due dates (newest first), then completed (newest first).
        """
        from datetime import datetime
        
        def parse_date(date_str):
            """Parse date string to datetime for sorting."""
            if not date_str:
                return None
            try:
                # Handle both formats: with and without time
                if ' ' in date_str:
                    return datetime.strptime(date_str, '%Y-%m-%d %H:%M')
                else:
                    return datetime.strptime(date_str, '%Y-%m-%d')
            except:
                return None
        
        def sort_key(task):
            """Generate sort key for task."""
            internal_status = task.get('_internal_status', 'pending')
            is_completed = internal_status == 'completed'
            
            created_date = parse_date(task.get('created_date', ''))
            due_date = parse_date(task.get('due_date', ''))
            
            if is_completed:
                # Completed tasks: newest created first
                # Return tuple: (completed=1, -created_timestamp)
                created_timestamp = created_date.timestamp() if created_date else 0
                return (1, -created_timestamp)
            else:
                # Pending tasks
                if due_date:
                    # Pending with due date: earliest due date first
                    # Return tuple: (pending=0, due_timestamp, -created_timestamp)
                    created_timestamp = created_date.timestamp() if created_date else 0
                    return (0, due_date.timestamp(), -created_timestamp)
                else:
                    # Pending without due date: newest created first
                    # Return tuple: (pending=0, very_large_number, -created_timestamp)
                    created_timestamp = created_date.timestamp() if created_date else 0
                    return (0, float('inf'), -created_timestamp)
        
        return sorted(tasks, key=sort_key)

    def _get_status_emoji(self, task: Dict, verbose: bool = False) -> str:
        """Get status emoji for a task."""
        if verbose:
            status = task.get('status', 'pending')
            return "√&nbsp;&nbsp;" if status == 'completed' else ""
        else:
            # For clean mode, check if we have an internal status field
            internal_status = task.get('_internal_status')
            if internal_status:
                return "√&nbsp;&nbsp;" if internal_status == 'completed' else ""
            return ""  # Default to no emoji for pending

    def _get_priority_emoji(self, priority: str) -> str:
        """Get priority emoji."""
        priority_lower = priority.lower()
        if priority_lower == 'high':
            return "🔴"
        elif priority_lower == 'medium':
            return "🟡"
        else:
            return "🟢"

    def _format_task_title(self, task: Dict) -> str:
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
            # Convert from milliseconds to seconds
            timestamp_seconds = int(timestamp) / 1000
            dt = datetime.fromtimestamp(timestamp_seconds)
            
            if include_seconds:
                return dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                return dt.strftime('%Y-%m-%d %H:%M')
        except (ValueError, TypeError):
            return 'Invalid date'

    def _extract_pretty_data(self, tasks_data: Dict, verbose: bool = False) -> Dict:
        """
        Extract human-readable task information from raw API data.
        
        Args:
            tasks_data: Raw task data from Any.do API
            verbose: Include all fields if True, clean output if False
            
        Returns:
            Dictionary with clean task data for markdown export
        """
        try:
            # Extract basic info
            export_info = {
                'extracted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'total_tasks': 0,
                'pending_tasks': 0,
                'completed_tasks': 0
            }
            
            # Extract list information
            lists_info = {}
            if 'models' in tasks_data and 'category' in tasks_data['models']:
                for list_item in tasks_data['models']['category']['items']:
                    list_name = list_item.get('name', 'Unknown List')
                    list_info = {
                        'task_count': 0,
                        'pending_count': 0,
                        'completed_count': 0
                    }
                    
                    # Add verbose fields for lists
                    if verbose:
                        list_info.update({
                            'color': list_item.get('color'),
                            'is_default': list_item.get('isDefault', False)
                        })
                    
                    lists_info[list_name] = list_info
            
            # Extract and organize tasks
            all_tasks = []
            tasks_by_id = {}  # For building parent-child relationships
            
            if 'models' in tasks_data and 'task' in tasks_data['models']:
                for task in tasks_data['models']['task']['items']:
                    task_id = task.get('globalTaskId')
                    parent_id = task.get('parentGlobalTaskId')
                    
                    # Build task info
                    task_info = {
                        'title': task.get('title', 'Untitled Task'),
                        'id': task_id,
                        'parent_id': parent_id
                    }
                    
                    # Add creation and update dates (always included)
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
                    
                    # Add due date if present
                    if task.get('dueDate'):
                        if verbose:
                            task_info['due_date'] = self._format_timestamp(task['dueDate'], include_seconds=True)
                        else:
                            task_info['due_date'] = self._format_timestamp(task['dueDate'], include_seconds=False)
                    
                    # Add list name
                    list_name = 'Unknown List'
                    if task.get('categoryId') and 'models' in tasks_data and 'category' in tasks_data['models']:
                        for list_item in tasks_data['models']['category']['items']:
                            if list_item.get('id') == task['categoryId']:
                                list_name = list_item.get('name', 'Unknown List')
                                break
                    task_info['list_name'] = list_name
                    
                    # Add note if present (always included when not empty)
                    note = task.get('note')
                    if note and note.strip():
                        task_info['note'] = note.strip()
                    
                    # Add tags if present
                    if task.get('labels'):
                        task_info['tags'] = task['labels']
                    
                    # Always store status internally for markdown generation
                    task_info['_internal_status'] = 'completed' if task.get('status') == 'CHECKED' else 'pending'
                    
                    # Add verbose fields
                    if verbose:
                        task_info.update({
                            'status': 'completed' if task.get('status') == 'CHECKED' else 'pending',
                            'priority': task.get('priority', 'Normal').lower(),
                            'list_color': None,  # Will be filled in later
                            'assignee': task.get('assignedTo'),
                            'repeating': task.get('repeatingMethod', 'TASK_REPEAT_OFF')
                        })
                        
                        # Add list color for verbose mode
                        if task.get('categoryId') and 'models' in tasks_data and 'category' in tasks_data['models']:
                            for list_item in tasks_data['models']['category']['items']:
                                if list_item.get('id') == task['categoryId']:
                                    task_info['list_color'] = list_item.get('color')
                                    break
                    
                    # Update counters
                    is_completed = task.get('status') == 'CHECKED'
                    export_info['total_tasks'] += 1
                    if is_completed:
                        export_info['completed_tasks'] += 1
                    else:
                        export_info['pending_tasks'] += 1
                    
                    # Update list counters
                    if list_name in lists_info:
                        lists_info[list_name]['task_count'] += 1
                        if is_completed:
                            lists_info[list_name]['completed_count'] += 1
                        else:
                            lists_info[list_name]['pending_count'] += 1
                    
                    # Store task for relationship building
                    tasks_by_id[task_id] = task_info
                    all_tasks.append(task_info)
            
            # Build parent-child relationships
            parent_tasks = []
            subtasks_by_parent = {}
            
            # Group subtasks by parent
            for task in all_tasks:
                if task['parent_id'] is None:
                    # This is a parent task
                    parent_tasks.append(task)
                else:
                    # This is a subtask
                    parent_id = task['parent_id']
                    if parent_id not in subtasks_by_parent:
                        subtasks_by_parent[parent_id] = []
                    subtasks_by_parent[parent_id].append(task)
            
            # Add subtasks to parent tasks
            for parent_task in parent_tasks:
                parent_id = parent_task['id']
                if parent_id in subtasks_by_parent:
                    # Sort subtasks by title
                    subtasks = sorted(subtasks_by_parent[parent_id], key=lambda x: x['title'])
                    
                    # Remove internal fields from subtasks
                    for subtask in subtasks:
                        subtask.pop('id', None)
                        subtask.pop('parent_id', None)
                    parent_task['subtasks'] = subtasks
            
            # Remove internal fields from parent tasks
            for task in parent_tasks:
                task.pop('id', None)
                task.pop('parent_id', None)
            
            # Group tasks by list
            tasks_by_list = {}
            for task in parent_tasks:
                list_name = task['list_name']
                if list_name not in tasks_by_list:
                    tasks_by_list[list_name] = []
                tasks_by_list[list_name].append(task)
            
            # Sort tasks within each list
            for list_name in tasks_by_list:
                tasks_by_list[list_name].sort(key=lambda x: x['title'])
            
            # Build final structure
            result = {
                'export_info': export_info,
                'lists': lists_info,
                'tasks': tasks_by_list
            }
            
            return result
            
        except Exception as e:
            print(f"⚠️  Error extracting pretty data: {e}")
            return {
                'export_info': {'error': str(e)},
                'lists': {},
                'tasks': {}
            }

    def get_simple_tasks(self) -> List[Dict[str, Any]]:
        """
        Get a simplified list of tasks with just the essential information.
        
        Returns:
            List of task dictionaries with title, completed status, due date, etc.
        """
        tasks_data = self.get_tasks()
        if not tasks_data:
            return []
        
        simple_tasks = []
        
        # Extract tasks from the response structure
        # Any.do returns data in models.task.items format
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
        
        # Fallback for other possible structures
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
    
    def get_lists(self) -> List[Dict[str, Any]]:
        """
        Get all task lists/categories.
        
        Returns:
            List of list dictionaries
        """
        tasks_data = self.get_tasks()
        if not tasks_data:
            return []
        
        lists = []
        
        # Any.do returns data in models.category.items format
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
                # Only include non-deleted lists
                if not list_info['is_deleted']:
                    lists.append(list_info)
        
        # Fallback for other possible structures
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
    
    def print_tasks_summary(self) -> None:
        """Print a nice summary of all tasks."""
        tasks = self.get_simple_tasks()
        lists = self.get_lists()
        
        if not tasks:
            print("No tasks found.")
            return
        
        # Create a mapping of list IDs to names
        list_names = {lst['id']: lst['name'] for lst in lists}
        
        print(f"\n=== Your Any.do Tasks ({len(tasks)} total) ===")
        
        # Group tasks by completion status
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
            for task in completed_tasks[:5]:  # Show only first 5 completed
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
            markdown_safe: If True, use <br> instead of \n for line breaks (for markdown tables)
            truncate_long_lines: If True, truncate very long lines instead of wrapping them
            
        Returns:
            Wrapped text
        """
        if not text:
            return text
            
        # Use larger wrap width for markdown tables to avoid ugly short wrapping
        wrap_width = width or (100 if markdown_safe else self.text_wrap_width)
        
        # For markdown tables with truncation (mainly task titles)
        if markdown_safe and truncate_long_lines:
            # Split text by existing line breaks first
            lines = text.split('\n')
            processed_lines = []
            
            for line in lines:
                if len(line) <= wrap_width:
                    processed_lines.append(line)
                else:
                    # Truncate long lines with ellipsis
                    truncated = line[:wrap_width-3] + "..."
                    processed_lines.append(truncated)
            
            return '<br>'.join(processed_lines)
        elif markdown_safe:
            # For markdown tables with wrapping (notes, subtasks)
            lines = text.split('\n')
            all_wrapped_lines = []
            
            for line in lines:
                if len(line) <= wrap_width:
                    all_wrapped_lines.append(line)
                else:
                    # Use textwrap to wrap long lines
                    wrapped_lines = textwrap.wrap(line, width=wrap_width, 
                                                break_long_words=False, 
                                                break_on_hyphens=False)
                    all_wrapped_lines.extend(wrapped_lines)
            
            return '<br>'.join(all_wrapped_lines)
        else:
            # For non-markdown, use normal wrapping
            lines = text.split('\n')
            all_wrapped_lines = []
            
            for line in lines:
                if len(line) <= wrap_width:
                    all_wrapped_lines.append(line)
                else:
                    # Use textwrap to wrap long lines
                    wrapped_lines = textwrap.wrap(line, width=wrap_width, 
                                                break_long_words=False, 
                                                break_on_hyphens=False)
                    all_wrapped_lines.extend(wrapped_lines)
            
            return '\n'.join(all_wrapped_lines) 
