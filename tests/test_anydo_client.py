#!/usr/bin/env python3
"""
Unit tests for the AnyDoClient library.

Run with: python -m pytest test_anydo_client.py -v
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, mock_open, ANY, call
import json
import os
import tempfile
import requests
from anydo_client import AnyDoClient


@patch('anydo_client.AnyDoClient._load_session', return_value=False)
@patch('anydo_client.AnyDoClient._test_session', return_value=False)
@patch('anydo_client.AnyDoClient._save_session')
@patch.object(requests.Session, 'get')
@patch.object(requests.Session, 'post')
class TestAnyDoClient(unittest.TestCase):
    """Test cases for AnyDoClient class."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Use a temporary session file for testing
        self.temp_session_file = tempfile.NamedTemporaryFile(delete=False).name
        
        # Create client with mocked network methods (handled by class decorators)
        self.client = AnyDoClient(session_file=self.temp_session_file)
        
        # Sample test data
        self.sample_user_data = {
            "email": "test@example.com",
            "name": "Test User",
            "id": "test_user_id",
            "isPremium": False
        }
        
        self.sample_sync_response = {
            "task_id": "test-sync-task-id-123",
            "total_timeout": 60,
            "polling_interval": 1
        }
        
        self.sample_tasks_data = {
            "models": {
                "task": {
                    "items": [
                        {
                            "id": "task1",
                            "title": "Buy groceries",
                            "status": "UNCHECKED",
                            "priority": "NORMAL",
                            "categoryId": "list1",
                            "dueDate": "2024-01-15"
                        },
                        {
                            "id": "task2", 
                            "title": "Complete project",
                            "status": "CHECKED",
                            "priority": "HIGH",
                            "categoryId": "list2",
                            "dueDate": None
                        }
                    ]
                },
                "category": {
                    "items": [
                        {
                            "id": "list1",
                            "name": "Personal",
                            "color": "blue",
                            "isDefault": True,
                            "isDeleted": False
                        },
                        {
                            "id": "list2",
                            "name": "Work",
                            "color": "red",
                            "isDefault": False,
                            "isDeleted": False
                        }
                    ]
                }
            }
        }
    
    def tearDown(self):
        """Clean up test fixtures."""
        # Remove temporary session file
        if os.path.exists(self.temp_session_file):
            os.unlink(self.temp_session_file)
    
    def test_init(self):
        """Test client initialization."""
        self.assertIsInstance(self.client.session, requests.Session)
        self.assertEqual(self.client.base_url, "https://sm-prod4.any.do")
        self.assertFalse(self.client.logged_in)
        self.assertIsNone(self.client.user_info)
        self.assertEqual(self.client.session_file, self.temp_session_file)
        self.assertEqual(self.client.text_wrap_width, 80)  # Default width
        
        # Test custom text wrap width
        client_custom = AnyDoClient(text_wrap_width=60)
        self.assertEqual(client_custom.text_wrap_width, 60)
    
    @patch('anydo_client.AnyDoClient._save_session')
    @patch('anydo_client.AnyDoClient._get_user_info')
    def test_login_success(self, mock_get_user_info, mock_save_session):
        """Test successful login without 2FA."""
        # Mock the login response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"requires2FA": False}
        
        with patch.object(self.client.session, 'post', return_value=mock_response):
            result = self.client.login("test@example.com", "password123")
            
            self.assertTrue(result)
            self.assertTrue(self.client.logged_in)
            mock_get_user_info.assert_called_once()
            mock_save_session.assert_called_once()
    
    @patch('anydo_client.AnyDoClient._handle_2fa_interactive')
    def test_login_with_2fa(self, mock_handle_2fa):
        """Test login that requires 2FA."""
        # Mock the login response requiring 2FA
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"requires2FA": True}
        
        mock_handle_2fa.return_value = True
        
        with patch.object(self.client.session, 'post', return_value=mock_response):
            result = self.client.login("test@example.com", "password123")
            
            self.assertTrue(result)
            mock_handle_2fa.assert_called_once()
    
    def test_login_failure(self):
        """Test login failure."""
        # Mock failed login response
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Invalid credentials"
        
        with patch('anydo_client.AnyDoClient._try_alternative_login', return_value=False):
            with patch.object(self.client.session, 'post', return_value=mock_response):
                result = self.client.login("test@example.com", "wrongpassword")
                
                self.assertFalse(result)
                self.assertFalse(self.client.logged_in)
    
    @patch('anydo_client.AnyDoClient._test_session')
    def test_login_with_valid_session(self, mock_test_session):
        """Test login when already logged in with valid session."""
        self.client.logged_in = True
        mock_test_session.return_value = True
        
        result = self.client.login("test@example.com", "password123")
        
        self.assertTrue(result)
        mock_test_session.assert_called_once()
    
    @patch('builtins.input', side_effect=['123456'])
    @patch('anydo_client.AnyDoClient._verify_2fa_code')
    @patch('anydo_client.AnyDoClient._save_session')
    @patch('anydo_client.AnyDoClient._get_user_info')
    def test_handle_2fa_interactive_success(self, mock_get_user_info, mock_save_session, mock_verify_2fa, mock_input):
        """Test successful interactive 2FA verification."""
        mock_verify_2fa.return_value = True
        
        result = self.client._handle_2fa_interactive()
        
        self.assertTrue(result)
        self.assertTrue(self.client.logged_in)
        mock_verify_2fa.assert_called_once_with('123456')
        mock_get_user_info.assert_called_once()
        mock_save_session.assert_called_once()
    
    @patch('builtins.input', side_effect=['123456', '654321', '111111'])
    @patch('anydo_client.AnyDoClient._verify_2fa_code')
    def test_handle_2fa_interactive_failure(self, mock_verify_2fa, mock_input):
        """Test failed interactive 2FA verification."""
        mock_verify_2fa.return_value = False
        
        result = self.client._handle_2fa_interactive()
        
        self.assertFalse(result)
        self.assertFalse(self.client.logged_in)
        # Should try 3 times
        self.assertEqual(mock_verify_2fa.call_count, 3)
    
    @patch('builtins.input', side_effect=[''])
    def test_handle_2fa_interactive_empty_code(self, mock_input):
        """Test interactive 2FA with empty code."""
        with patch('builtins.input', side_effect=['', '123456']):
            with patch('anydo_client.AnyDoClient._verify_2fa_code', return_value=True):
                with patch('anydo_client.AnyDoClient._get_user_info'):
                    with patch('anydo_client.AnyDoClient._save_session'):
                        result = self.client._handle_2fa_interactive()
                        self.assertTrue(result)
    
    def test_verify_2fa_code_success(self):
        """Test successful 2FA code verification."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        
        with patch.object(self.client.session, 'post', return_value=mock_response):
            result = self.client._verify_2fa_code("123456")
            
            self.assertTrue(result)
    
    def test_verify_2fa_code_failure(self):
        """Test failed 2FA code verification."""
        mock_response = Mock()
        mock_response.status_code = 400
        
        with patch.object(self.client.session, 'post', return_value=mock_response):
            result = self.client._verify_2fa_code("wrong_code")
            
            self.assertFalse(result)
    
    def test_save_session(self):
        """Test session saving."""
        self.client.user_info = self.sample_user_data
        
        # Mock cookies
        mock_cookie = Mock()
        mock_cookie.name = "session_id"
        mock_cookie.value = "abc123"
        mock_cookie.domain = "any.do"
        mock_cookie.path = "/"
        
        # Mock the cookies jar
        with patch.object(self.client.session, 'cookies', [mock_cookie]):
            with patch('builtins.open', mock_open()) as mock_file:
                with patch('json.dump') as mock_json_dump:
                    self.client._save_session()
                    
                    mock_file.assert_called_once_with(self.temp_session_file, 'w')
                    # Check that json.dump was called with the right structure
                    mock_json_dump.assert_called_once()
                    call_args = mock_json_dump.call_args[0][0]
                    self.assertIn('cookies', call_args)
                    self.assertIn('user_info', call_args)
    
    def test_load_session(self):
        """Test session loading."""
        session_data = {
            "cookies": [
                {
                    "name": "session_id",
                    "value": "abc123",
                    "domain": "any.do",
                    "path": "/"
                }
            ],
            "user_info": self.sample_user_data
        }
        
        with patch('builtins.open', mock_open(read_data=json.dumps(session_data))):
            with patch('os.path.exists', return_value=True):
                with patch.object(self.client, '_test_session', return_value=True):
                    result = self.client._load_session()
                    
                    self.assertTrue(result)
                    self.assertTrue(self.client.logged_in)
                    self.assertEqual(self.client.user_info, self.sample_user_data)
    
    def test_test_session(self):
        """Test session validity testing."""
        mock_response = Mock()
        mock_response.status_code = 200
        
        with patch.object(self.client.session, 'get', return_value=mock_response):
            result = self.client._test_session()
            self.assertTrue(result)
    
    def test_clear_session(self):
        """Test session clearing."""
        self.client.user_info = self.sample_user_data
        self.client.logged_in = True
        
        with patch('os.path.exists', return_value=True):
            with patch('os.remove') as mock_remove:
                self.client._clear_session()
                
                self.assertIsNone(self.client.user_info)
                self.assertFalse(self.client.logged_in)
                mock_remove.assert_called_once_with(self.temp_session_file)
    
    def test_get_user_info(self):
        """Test getting user information."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = self.sample_user_data
        
        with patch.object(self.client.session, 'get', return_value=mock_response):
            self.client._get_user_info()
            
            self.assertEqual(self.client.user_info, self.sample_user_data)
    
    def test_get_tasks_not_logged_in(self):
        """Test getting tasks when not logged in."""
        result = self.client.get_tasks()
        self.assertIsNone(result)
    
    @patch('time.sleep')  # Mock sleep to speed up tests
    def test_get_tasks_success(self, mock_sleep):
        """Test successful task retrieval."""
        self.client.logged_in = True
        
        # Mock sync request
        mock_sync_response = Mock()
        mock_sync_response.status_code = 200
        mock_sync_response.json.return_value = self.sample_sync_response
        mock_sync_response.raise_for_status = Mock()
        
        # Mock tasks result request
        mock_tasks_response = Mock()
        mock_tasks_response.status_code = 200
        mock_tasks_response.json.return_value = self.sample_tasks_data
        mock_tasks_response.raise_for_status = Mock()
        
        with patch.object(self.client.session, 'get', side_effect=[mock_sync_response, mock_tasks_response]):
            result = self.client.get_tasks()
            
            self.assertEqual(result, self.sample_tasks_data)
            mock_sleep.assert_called_once_with(1)
    
    def test_get_tasks_no_task_id(self):
        """Test task retrieval when sync doesn't return task_id."""
        self.client.logged_in = True
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}  # No task_id
        mock_response.raise_for_status = Mock()
        
        with patch.object(self.client.session, 'get', return_value=mock_response):
            result = self.client.get_tasks()
            
            self.assertIsNone(result)
    
    def test_get_simple_tasks(self):
        """Test getting simplified task list."""
        with patch.object(self.client, 'get_tasks', return_value=self.sample_tasks_data):
            result = self.client.get_simple_tasks()
            
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]['title'], 'Buy groceries')
            self.assertFalse(result[0]['completed'])  # UNCHECKED
            self.assertEqual(result[1]['title'], 'Complete project')
            self.assertTrue(result[1]['completed'])  # CHECKED
    
    def test_get_simple_tasks_no_data(self):
        """Test getting simple tasks when no data available."""
        with patch.object(self.client, 'get_tasks', return_value=None):
            result = self.client.get_simple_tasks()
            self.assertEqual(result, [])
    
    def test_get_lists(self):
        """Test getting task lists."""
        with patch.object(self.client, 'get_tasks', return_value=self.sample_tasks_data):
            result = self.client.get_lists()
            
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]['name'], 'Personal')
            self.assertTrue(result[0]['is_default'])
            self.assertEqual(result[1]['name'], 'Work')
            self.assertFalse(result[1]['is_default'])
    
    def test_get_lists_no_data(self):
        """Test getting lists when no data available."""
        with patch.object(self.client, 'get_tasks', return_value=None):
            result = self.client.get_lists()
            self.assertEqual(result, [])
    
    def test_calculate_data_hash(self):
        """Test data hash calculation."""
        hash1 = self.client._calculate_data_hash(self.sample_tasks_data)
        hash2 = self.client._calculate_data_hash(self.sample_tasks_data)
        
        # Same data should produce same hash
        self.assertEqual(hash1, hash2)
        
        # Different data should produce different hash
        different_data = {"different": "data"}
        hash3 = self.client._calculate_data_hash(different_data)
        self.assertNotEqual(hash1, hash3)
    
    @patch('os.makedirs')
    @patch('os.path.getsize')
    def test_save_tasks_to_file(self, mock_getsize, mock_makedirs):
        """Test saving tasks to timestamped file."""
        mock_getsize.return_value = 1024  # 1KB
        
        with patch('builtins.open', mock_open()) as mock_file:
            with patch('anydo_client.datetime') as mock_datetime:
                mock_datetime.now.return_value.strftime.return_value = "2024-01-15_1430-45"
                
                result = self.client.save_tasks_to_file(self.sample_tasks_data)
                
                expected_path = os.path.join("outputs/raw-json", "2024-01-15_1430-45_anydo-tasks.json")
                self.assertEqual(result, expected_path)
                
                # Should create both JSON and markdown files
                expected_calls = [
                    call(expected_path, 'w', encoding='utf-8'),
                    call('outputs/markdown/2024-01-15_1430-45_anydo-tasks.md', 'w', encoding='utf-8')
                ]
                mock_file.assert_has_calls(expected_calls, any_order=True)
                
                # Verify directories were created
                mock_makedirs.assert_has_calls([
                    call("outputs/raw-json", exist_ok=True),
                    call("outputs/markdown", exist_ok=True)
                ], any_order=True)
    
    def test_save_tasks_to_file_no_changes(self):
        """Test saving tasks when no changes detected."""
        # Set an existing hash
        self.client.last_data_hash = self.client._calculate_data_hash(self.sample_tasks_data)
        
        result = self.client.save_tasks_to_file(self.sample_tasks_data)
        
        self.assertIsNone(result)
    
    def test_save_tasks_to_file_same_hash(self):
        """Test saving tasks when data hash is the same (no changes)."""
        # Set an existing hash to simulate no changes
        self.client.last_data_hash = self.client._calculate_data_hash(self.sample_tasks_data)
        
        # Should return None (no save) when no changes detected
        result = self.client.save_tasks_to_file(self.sample_tasks_data)
        self.assertIsNone(result)
    
    def test_save_tasks_to_file_no_data(self):
        """Test saving tasks with no data."""
        result = self.client.save_tasks_to_file({})  # Empty dict instead of None
        self.assertIsNone(result)
    
    @patch('os.makedirs')
    @patch('os.path.getsize')
    @patch('anydo_client.AnyDoClient._save_markdown_from_json')
    def test_save_tasks_to_file_with_markdown(self, mock_save_markdown, mock_getsize, mock_makedirs):
        """Test saving tasks also generates markdown."""
        mock_getsize.return_value = 1024  # 1KB
        mock_save_markdown.return_value = "outputs/markdown/2024-01-15_1430-45_anydo-tasks.md"
        
        with patch('builtins.open', mock_open()) as mock_file:
            with patch('anydo_client.datetime') as mock_datetime:
                mock_datetime.now.return_value.strftime.return_value = "2024-01-15_1430-45"
                
                result = self.client.save_tasks_to_file(self.sample_tasks_data)
                
                expected_path = os.path.join("outputs/raw-json", "2024-01-15_1430-45_anydo-tasks.json")
                self.assertEqual(result, expected_path)
                mock_save_markdown.assert_called_once_with(self.sample_tasks_data, "2024-01-15_1430-45")



    def test_extract_pretty_data_basic(self):
        """Test basic pretty data extraction."""
        tasks_data = {
            "models": {
                "task": {
                    "items": [
                        {
                            "id": "task1",
                            "globalTaskId": "task1",
                            "title": "Test Task",
                            "status": "UNCHECKED",
                            "priority": "HIGH",
                            "creationDate": "1640995200000",  # 2022-01-01 00:00:00
                            "lastUpdateDate": "1640995200000",
                            "categoryId": "cat1",
                            "labels": ["work", "urgent"],
                            "note": "Important task",
                            "parentGlobalTaskId": None
                        }
                    ]
                },
                "category": {
                    "items": [
                        {
                            "id": "cat1",
                            "name": "Work",
                            "color": "blue",
                            "isDefault": False
                        }
                    ]
                }
            }
        }
        
        # Test clean mode (default)
        pretty_data = self.client._extract_pretty_data(tasks_data, verbose=False)
        
        # Check export info
        self.assertEqual(pretty_data["export_info"]["total_tasks"], 1)
        self.assertEqual(pretty_data["export_info"]["pending_tasks"], 1)
        self.assertEqual(pretty_data["export_info"]["completed_tasks"], 0)
        
        # Check lists - should use name as key, not ID
        self.assertIn("Work", pretty_data["lists"])
        self.assertNotIn("cat1", pretty_data["lists"])
        self.assertEqual(pretty_data["lists"]["Work"]["task_count"], 1)
        
        # Check that verbose fields are excluded in clean mode
        self.assertNotIn("color", pretty_data["lists"]["Work"])
        self.assertNotIn("is_default", pretty_data["lists"]["Work"])
        
        # Check tasks - now organized by list name
        self.assertIn("Work", pretty_data["tasks"])
        self.assertEqual(len(pretty_data["tasks"]["Work"]), 1)
        task = pretty_data["tasks"]["Work"][0]
        
        # Check basic task fields
        self.assertEqual(task["title"], "Test Task")
        self.assertEqual(task["list_name"], "Work")
        self.assertEqual(task["note"], "Important task")
        self.assertEqual(task["tags"], ["work", "urgent"])
        
        # Check dates (should not include seconds in clean mode)
        self.assertEqual(task["created_date"], "2022-01-01 00:00")
        self.assertEqual(task["last_update"], "2022-01-01 00:00")
        
        # Check that verbose fields are excluded in clean mode
        self.assertNotIn("status", task)
        self.assertNotIn("priority", task)
        self.assertNotIn("list_color", task)
        self.assertNotIn("assignee", task)
        self.assertNotIn("repeating", task)
        
        # Check that internal fields are removed
        self.assertNotIn("id", task)
        self.assertNotIn("parent_id", task)

    def test_extract_pretty_data_verbose(self):
        """Test verbose pretty data extraction."""
        tasks_data = {
            "models": {
                "task": {
                    "items": [
                        {
                            "id": "task1",
                            "globalTaskId": "task1",
                            "title": "Test Task",
                            "status": "CHECKED",
                            "priority": "HIGH",
                            "creationDate": "1640995200000",
                            "lastUpdateDate": "1640995200000",
                            "categoryId": "cat1",
                            "assignedTo": "user@example.com",
                            "repeatingMethod": "TASK_REPEAT_WEEKLY",
                            "parentGlobalTaskId": None
                        }
                    ]
                },
                "category": {
                    "items": [
                        {
                            "id": "cat1",
                            "name": "Work",
                            "color": "blue",
                            "isDefault": True
                        }
                    ]
                }
            }
        }
        
        # Test verbose mode
        pretty_data = self.client._extract_pretty_data(tasks_data, verbose=True)
        
        # Check that verbose fields are included in lists
        self.assertIn("color", pretty_data["lists"]["Work"])
        self.assertIn("is_default", pretty_data["lists"]["Work"])
        self.assertEqual(pretty_data["lists"]["Work"]["color"], "blue")
        self.assertEqual(pretty_data["lists"]["Work"]["is_default"], True)
        
        # Check tasks
        task = pretty_data["tasks"]["Work"][0]
        
        # Check that verbose fields are included in tasks
        self.assertIn("status", task)
        self.assertIn("priority", task)
        self.assertIn("list_color", task)
        self.assertIn("assignee", task)
        self.assertIn("repeating", task)
        
        # Check verbose field values
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["priority"], "high")
        self.assertEqual(task["list_color"], "blue")
        self.assertEqual(task["assignee"], "user@example.com")
        self.assertEqual(task["repeating"], "TASK_REPEAT_WEEKLY")
        
        # Check date format (includes seconds in verbose mode)
        self.assertEqual(task["created_date"], "2022-01-01 00:00:00")
        self.assertEqual(task["last_update"], "2022-01-01 00:00:00")

    def test_extract_pretty_data_subtasks(self):
        """Test pretty data extraction with subtasks."""
        tasks_data = {
            "models": {
                "task": {
                    "items": [
                        {
                            "id": "task1",
                            "globalTaskId": "task1",
                            "title": "Main Task",
                            "status": "UNCHECKED",
                            "categoryId": "cat1",
                            "parentGlobalTaskId": None,
                            "subTasks": []  # Empty in real API
                        },
                        {
                            "id": "subtask1",
                            "globalTaskId": "subtask1",
                            "title": "Subtask 1",
                            "status": "CHECKED",
                            "categoryId": "cat1",
                            "parentGlobalTaskId": "task1",
                            "note": "First subtask",
                            "subTasks": []
                        },
                        {
                            "id": "subtask2",
                            "globalTaskId": "subtask2",
                            "title": "Subtask 2",
                            "status": "UNCHECKED",
                            "categoryId": "cat1",
                            "parentGlobalTaskId": "task1",
                            "subTasks": []
                        }
                    ]
                },
                "category": {
                    "items": [
                        {
                            "id": "cat1",
                            "name": "Personal",
                            "color": "green",
                            "isDefault": True
                        }
                    ]
                }
            }
        }
        
        pretty_data = self.client._extract_pretty_data(tasks_data, verbose=False)
        
        # Check tasks are organized by list name
        self.assertIn("Personal", pretty_data["tasks"])
        self.assertEqual(len(pretty_data["tasks"]["Personal"]), 1)  # Only parent task
        task = pretty_data["tasks"]["Personal"][0]
        
        # Check main task
        self.assertEqual(task["title"], "Main Task")
        
        # Check subtasks
        self.assertIn("subtasks", task)
        self.assertEqual(len(task["subtasks"]), 2)
        
        # Check subtask details
        subtask1 = task["subtasks"][0]
        self.assertEqual(subtask1["title"], "Subtask 1")
        self.assertEqual(subtask1["note"], "First subtask")
        
        subtask2 = task["subtasks"][1]
        self.assertEqual(subtask2["title"], "Subtask 2")
        self.assertNotIn("note", subtask2)  # No note for this subtask



    def test_session_save_with_hashes(self):
        """Test session saving includes hash tracking."""
        self.client.user_info = self.sample_user_data
        self.client.last_data_hash = "test_data_hash"
        self.client.last_pretty_hash = "test_pretty_hash"
        
        # Mock cookies
        mock_cookie = Mock()
        mock_cookie.name = "session_id"
        mock_cookie.value = "abc123"
        mock_cookie.domain = "any.do"
        mock_cookie.path = "/"
        
        with patch.object(self.client.session, 'cookies', [mock_cookie]):
            with patch('builtins.open', mock_open()) as mock_file:
                with patch('json.dump') as mock_json_dump:
                    self.client._save_session()
                    
                    mock_file.assert_called_once_with(self.temp_session_file, 'w')
                    mock_json_dump.assert_called_once()
                    call_args = mock_json_dump.call_args[0][0]
                    self.assertIn('last_data_hash', call_args)
                    self.assertIn('last_pretty_hash', call_args)
                    self.assertEqual(call_args['last_data_hash'], "test_data_hash")
                    self.assertEqual(call_args['last_pretty_hash'], "test_pretty_hash")

    def test_session_load_with_hashes(self):
        """Test session loading restores hash tracking."""
        session_data = {
            'cookies': [],
            'user_info': self.sample_user_data,
            'last_data_hash': 'loaded_data_hash',
            'last_pretty_hash': 'loaded_pretty_hash'
        }
        
        with patch('os.path.exists', return_value=True):
            with patch('builtins.open', mock_open(read_data=json.dumps(session_data))):
                with patch.object(self.client, '_test_session', return_value=True):
                    result = self.client._load_session()
                    
                    self.assertTrue(result)
                    self.assertEqual(self.client.last_data_hash, 'loaded_data_hash')
                    self.assertEqual(self.client.last_pretty_hash, 'loaded_pretty_hash')

    @patch('builtins.print')
    def test_print_tasks_summary(self, mock_print):
        """Test printing task summary."""
        with patch.object(self.client, 'get_simple_tasks', return_value=[
            {'title': 'Task 1', 'completed': False, 'priority': 'HIGH', 'list_id': 'list1', 'due_date': '2024-01-15'},
            {'title': 'Task 2', 'completed': True, 'priority': 'NORMAL', 'list_id': 'list2', 'due_date': None}
        ]):
            with patch.object(self.client, 'get_lists', return_value=[
                {'id': 'list1', 'name': 'Personal'},
                {'id': 'list2', 'name': 'Work'}
            ]):
                self.client.print_tasks_summary()
                
                # Check that print was called with task information
                mock_print.assert_called()
                print_calls = [call.args[0] for call in mock_print.call_args_list]
                self.assertTrue(any('Task 1' in call for call in print_calls))
                self.assertTrue(any('Task 2' in call for call in print_calls))
    
    @patch('builtins.print')
    def test_print_tasks_summary_no_tasks(self, mock_print):
        """Test printing task summary when no tasks available."""
        with patch.object(self.client, 'get_simple_tasks', return_value=[]):
            self.client.print_tasks_summary()
            
            mock_print.assert_called_with("No tasks found.")

    def test_save_markdown_tasks(self):
        """Test markdown table generation."""
        # Create test data
        test_data = {
            'export_info': {
                'extracted_at': '2024-01-01 12:00:00',
                'total_tasks': 3,
                'pending_tasks': 2,
                'completed_tasks': 1
            },
            'lists': {
                'Test List': {
                    'task_count': 3,
                    'pending_count': 2,
                    'completed_count': 1
                }
            },
            'tasks': {
                'Test List': [
                    {
                        'title': 'Test Task 1',
                        'created_date': '2024-01-01 10:00',
                        'last_update': '2024-01-01 11:00',
                        'due_date': '2024-01-02 12:00',
                        'note': 'Test note',
                        '_internal_status': 'pending'
                    },
                    {
                        'title': 'Test Task 2',
                        'created_date': '2024-01-01 10:30',
                        'last_update': '2024-01-01 11:30',
                        'due_date': '',
                        'note': '',
                        '_internal_status': 'completed',
                        'subtasks': [
                            {
                                'title': 'Subtask 1',
                                '_internal_status': 'pending'
                            },
                            {
                                'title': 'Subtask 2',
                                '_internal_status': 'completed'
                            }
                        ]
                    }
                ]
            }
        }
        
        # Test clean mode markdown
        result = self.client._save_markdown_tasks(test_data, "2024-01-01_1200-00", verbose=False)
        
        self.assertIsNotNone(result)
        if result:  # Type guard for mypy
            self.assertTrue(os.path.exists(result))
            
            # Read and verify markdown content
            with open(result, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Check for key elements
            self.assertIn('# 📋 Any.do Tasks Export (Clean Mode)', content)
            self.assertIn('## 📊 Export Summary', content)
            self.assertIn('## 📁 Lists Summary', content)
            self.assertIn('## 📝 Tasks', content)
            self.assertIn('| Title | List | Created | Due |', content)  # New single table header
            self.assertIn('Test Task 1', content)  # No status emoji for pending
            self.assertIn('√&nbsp;&nbsp;Test Task 2', content)  # New completed emoji
            self.assertIn('<span style="color: #666; font-style: italic;">Test note</span>', content)  # Note in title cell
            self.assertIn('&nbsp;&nbsp;&nbsp;- Subtask 1', content)  # New pending subtask format
            self.assertIn('&nbsp;&nbsp;&nbsp;√&nbsp;&nbsp;Subtask 2', content)  # New completed subtask format
            
            # Clean up
            os.remove(result)
        
        # Test verbose mode markdown
        result_verbose = self.client._save_markdown_tasks(test_data, "2024-01-01_1200-00", verbose=True)
        
        self.assertIsNotNone(result_verbose)
        if result_verbose:  # Type guard for mypy
            self.assertTrue(os.path.exists(result_verbose))
            
            # Read and verify verbose markdown content
            with open(result_verbose, 'r', encoding='utf-8') as f:
                content_verbose = f.read()
            
            # Check for verbose-specific elements
            self.assertIn('# 📋 Any.do Tasks Export (Verbose Mode)', content_verbose)
            self.assertIn('Priority', content_verbose)
            self.assertIn('Assignee', content_verbose)
            
            # Clean up
            os.remove(result_verbose)

    def test_generate_markdown_content(self):
        """Test markdown content generation."""
        test_data = {
            'export_info': {
                'extracted_at': '2024-01-01 12:00:00',
                'total_tasks': 2,
                'pending_tasks': 1,
                'completed_tasks': 1
            },
            'lists': {
                'Work': {
                    'task_count': 2,
                    'pending_count': 1,
                    'completed_count': 1
                }
            },
            'tasks': {
                'Work': [
                    {
                        'title': 'Meeting with team',
                        'created_date': '2024-01-01 09:00',
                        'last_update': '2024-01-01 10:00',
                        '_internal_status': 'pending'
                    },
                    {
                        'title': 'Review code',
                        'created_date': '2024-01-01 08:00',
                        'note': 'Check PR #123',
                        '_internal_status': 'completed'
                    }
                ]
            }
        }
        
        # Test clean mode
        content = self.client._generate_markdown_content(test_data, verbose=False)
        
        # Check header
        self.assertIn('# 📋 Any.do Tasks Export (Clean Mode)', content)
        self.assertIn('*Generated: 2024-01-01 12:00:00*', content)
        
        # Check export summary
        self.assertIn('## 📊 Export Summary', content)
        self.assertIn('| 📋 Total Tasks | 2 |', content)
        self.assertIn('| ⏳ Pending Tasks | 1 |', content)
        self.assertIn('| ✅ Completed Tasks | 1 |', content)
        
        # Check lists summary (no check symbols)
        self.assertIn('## 📁 Lists Summary', content)
        self.assertIn('| Work | 2 | 1 | 1 |', content)
        
        # Check tasks section with single table
        self.assertIn('## 📝 Tasks', content)
        self.assertIn('| Title | List | Created | Due |', content)
        
        # Check task content - pending task should appear first (no status emoji)
        self.assertIn('Meeting with team', content)
        # Check completed task with new emoji and note in title cell
        self.assertIn('√&nbsp;&nbsp;Review code <br> &nbsp;&nbsp;&nbsp;<span style="color: #666; font-style: italic;">Check PR #123</span>', content)

    def test_get_status_emoji(self):
        """Test status emoji generation."""
        # Test with internal status
        task_pending = {'_internal_status': 'pending'}
        task_completed = {'_internal_status': 'completed'}
        
        self.assertEqual(self.client._get_status_emoji(task_pending, verbose=False), '')
        self.assertEqual(self.client._get_status_emoji(task_completed, verbose=False), '√&nbsp;&nbsp;')
        
        # Test verbose mode
        task_verbose_pending = {'status': 'pending'}
        task_verbose_completed = {'status': 'completed'}
        
        self.assertEqual(self.client._get_status_emoji(task_verbose_pending, verbose=True), '')
        self.assertEqual(self.client._get_status_emoji(task_verbose_completed, verbose=True), '√&nbsp;&nbsp;')

    def test_get_priority_emoji(self):
        """Test priority emoji generation."""
        self.assertEqual(self.client._get_priority_emoji('high'), '🔴')
        self.assertEqual(self.client._get_priority_emoji('HIGH'), '🔴')
        self.assertEqual(self.client._get_priority_emoji('medium'), '🟡')
        self.assertEqual(self.client._get_priority_emoji('MEDIUM'), '🟡')
        self.assertEqual(self.client._get_priority_emoji('low'), '🟢')
        self.assertEqual(self.client._get_priority_emoji('normal'), '🟢')
        self.assertEqual(self.client._get_priority_emoji(''), '🟢')

    def test_format_task_title(self):
        """Test task title formatting with text wrapping."""
        # Test various task types - now should return titles with text wrapping applied
        test_cases = [
            ('Fix bug in login', 'Fix bug in login'),
            ('Team meeting tomorrow', 'Team meeting tomorrow'),
            ('Send email to client', 'Send email to client'),
            ('Buy groceries', 'Buy groceries'),
            ('Read documentation', 'Read documentation'),
            ('Exercise routine', 'Exercise routine'),
            ('Clean the house', 'Clean the house'),
            ('Cook dinner', 'Cook dinner'),
            ('Regular task', 'Regular task')
        ]
        
        for input_title, expected_output in test_cases:
            task = {'title': input_title}
            result = self.client._format_task_title(task)
            self.assertEqual(result, expected_output, f"Failed for title: {input_title}")
    
    def test_wrap_text(self):
        """Test text wrapping functionality."""
        # Test short text (no wrapping needed)
        short_text = "This is a short title"
        result = self.client._wrap_text(short_text)
        self.assertEqual(result, short_text)
        
        # Test long text (wrapping needed)
        long_text = "This is a very long task title that should definitely be wrapped at 80 characters because it exceeds the default width"
        result = self.client._wrap_text(long_text)
        lines = result.split('\n')
        # Each line should be <= 80 characters
        for line in lines:
            self.assertLessEqual(len(line), 80)
        
        # Test text with existing line breaks
        multiline_text = "Line one\nLine two that is much longer and should be wrapped appropriately\nLine three"
        result = self.client._wrap_text(multiline_text)
        lines = result.split('\n')
        for line in lines:
            self.assertLessEqual(len(line), 80)
        
        # Test custom width
        result_custom = self.client._wrap_text(long_text, width=40)
        lines_custom = result_custom.split('\n')
        for line in lines_custom:
            self.assertLessEqual(len(line), 40)

    def test_sort_tasks_for_display(self):
        """Test task sorting for display."""
        tasks = [
            {
                'title': 'Completed old task',
                'created_date': '2024-01-01 10:00',
                '_internal_status': 'completed'
            },
            {
                'title': 'Pending with due date',
                'created_date': '2024-01-02 10:00',
                'due_date': '2024-01-10 15:00',
                '_internal_status': 'pending'
            },
            {
                'title': 'Pending without due date',
                'created_date': '2024-01-03 10:00',
                '_internal_status': 'pending'
            },
            {
                'title': 'Completed new task',
                'created_date': '2024-01-04 10:00',
                '_internal_status': 'completed'
            },
            {
                'title': 'Pending with earlier due date',
                'created_date': '2024-01-01 10:00',
                'due_date': '2024-01-05 15:00',
                '_internal_status': 'pending'
            }
        ]
        
        sorted_tasks = self.client._sort_tasks_for_display(tasks)
        
        # Expected order:
        # 1. Pending with earlier due date (due 2024-01-05)
        # 2. Pending with due date (due 2024-01-10)
        # 3. Pending without due date (newest created: 2024-01-03)
        # 4. Completed new task (newest created: 2024-01-04)
        # 5. Completed old task (oldest created: 2024-01-01)
        
        expected_titles = [
            'Pending with earlier due date',
            'Pending with due date',
            'Pending without due date',
            'Completed new task',
            'Completed old task'
        ]
        
        actual_titles = [task['title'] for task in sorted_tasks]
        self.assertEqual(actual_titles, expected_titles)


class TestAnyDoClientIntegration(unittest.TestCase):
    """Integration tests for AnyDoClient (disabled to avoid server stress)."""
    
    def setUp(self):
        """Set up integration test fixtures."""
        # Always skip integration tests to avoid server stress
        self.skipTest("Integration tests disabled to avoid server stress and potential bans")
    
    def test_real_login_flow(self):
        """Test actual login flow - disabled to avoid server stress."""
        self.skipTest("Integration tests disabled to avoid server stress and potential bans")


if __name__ == '__main__':
    unittest.main() 
