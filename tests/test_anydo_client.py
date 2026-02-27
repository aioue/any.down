#!/usr/bin/env python3
"""
Unit tests for the AnyDoClient library.

Run with: pytest tests/test_anydo_client.py -v
"""

import json
import os
import tempfile
import unittest
from unittest.mock import Mock, call, mock_open, patch

import requests

from anydown.client import AnyDoClient

# Shared sample data used across test classes
SAMPLE_USER_DATA = {
    "email": "test@example.com",
    "name": "Test User",
    "id": "test_user_id",
    "isPremium": False,
}

SAMPLE_SYNC_RESPONSE = {
    "task_id": "test-sync-task-id-123",
    "total_timeout": 60,
    "polling_interval": 1,
}

SAMPLE_TASKS_DATA = {
    "models": {
        "task": {
            "items": [
                {
                    "id": "task1",
                    "title": "Buy groceries",
                    "status": "UNCHECKED",
                    "priority": "NORMAL",
                    "categoryId": "list1",
                    "dueDate": "2024-01-15",
                },
                {
                    "id": "task2",
                    "title": "Complete project",
                    "status": "CHECKED",
                    "priority": "HIGH",
                    "categoryId": "list2",
                    "dueDate": None,
                },
            ]
        },
        "category": {
            "items": [
                {
                    "id": "list1",
                    "name": "Personal",
                    "color": "blue",
                    "isDefault": True,
                    "isDeleted": False,
                },
                {
                    "id": "list2",
                    "name": "Work",
                    "color": "red",
                    "isDefault": False,
                    "isDeleted": False,
                },
            ]
        },
    }
}


class TestAnyDoClient(unittest.TestCase):
    """Test cases for AnyDoClient class."""

    def setUp(self):
        self.temp_session_file = tempfile.NamedTemporaryFile(delete=False).name
        with patch.object(AnyDoClient, "_load_session", return_value=False):
            self.client = AnyDoClient(session_file=self.temp_session_file)

    def tearDown(self):
        if os.path.exists(self.temp_session_file):
            os.unlink(self.temp_session_file)

    def test_init(self):
        with patch.object(AnyDoClient, "_load_session", return_value=False):
            client = AnyDoClient(session_file=self.temp_session_file)

            self.assertIsInstance(client.session, requests.Session)
            self.assertEqual(client.base_url, "https://sm-prod4.any.do")
            self.assertFalse(client.logged_in)
            self.assertIsNone(client.user_info)
            self.assertEqual(client.session_file, self.temp_session_file)
            self.assertEqual(client.text_wrap_width, 80)

            client_custom = AnyDoClient(text_wrap_width=60)
            self.assertEqual(client_custom.text_wrap_width, 60)

    # -------------------------------------------------------------------------
    # Authentication tests
    # -------------------------------------------------------------------------

    def test_login_success(self):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"user_exists": True}

        with patch.object(self.client.session, "post", return_value=mock_response):
            with patch.object(self.client, "_trigger_2fa_email", return_value=True):
                with patch.object(self.client, "_handle_2fa_interactive", return_value=True):
                    result = self.client.login("test@example.com", "password123")
                    self.assertTrue(result)

    def test_login_with_2fa(self):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"user_exists": True}

        with patch.object(self.client.session, "post", return_value=mock_response):
            with patch.object(self.client, "_trigger_2fa_email", return_value=True):
                with patch.object(self.client, "_handle_2fa_interactive", return_value=True) as mock_handle_2fa:
                    result = self.client.login("test@example.com", "password123")
                    self.assertTrue(result)
                    mock_handle_2fa.assert_called_once_with("test@example.com", "password123")

    def test_login_failure(self):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"user_exists": False}

        with patch.object(self.client.session, "post", return_value=mock_response):
            with patch("time.sleep"):
                result = self.client.login("test@example.com", "wrongpassword")
                self.assertFalse(result)
                self.assertFalse(self.client.logged_in)

    def test_login_with_valid_session(self):
        self.client.logged_in = True
        with patch.object(self.client, "_test_session", return_value=True):
            result = self.client.login("test@example.com", "password123")
            self.assertTrue(result)

    def test_handle_2fa_interactive_success(self):
        with patch.object(self.client, "_trigger_2fa_email", return_value=True):
            with patch("builtins.input", return_value="123456"):
                with patch.object(self.client, "_verify_2fa_code", return_value=True):
                    with patch.object(self.client, "_get_user_info"):
                        with patch.object(self.client, "_save_session"):
                            result = self.client._handle_2fa_interactive("test@example.com", "password123")
                            self.assertTrue(result)
                            self.assertTrue(self.client.logged_in)

    def test_handle_2fa_interactive_failure(self):
        with patch.object(self.client, "_trigger_2fa_email", return_value=True):
            with patch("builtins.input", side_effect=["123456", "654321", "111111"]):
                with patch.object(self.client, "_verify_2fa_code", return_value=False):
                    result = self.client._handle_2fa_interactive("test@example.com", "password123")
                    self.assertFalse(result)
                    self.assertFalse(self.client.logged_in)

    def test_handle_2fa_interactive_empty_code(self):
        with patch.object(self.client, "_trigger_2fa_email", return_value=True):
            with patch("builtins.input", side_effect=["", "123456"]):
                with patch.object(self.client, "_verify_2fa_code", return_value=True):
                    with patch.object(self.client, "_get_user_info"):
                        with patch.object(self.client, "_save_session"):
                            result = self.client._handle_2fa_interactive("test@example.com", "password123")
                            self.assertTrue(result)

    def test_verify_2fa_code_success(self):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"auth_token": "test_token"}

        with patch.object(self.client.session, "post", return_value=mock_response):
            with patch("time.sleep"):
                result = self.client._verify_2fa_code("test@example.com", "password123", "123456")
                self.assertTrue(result)
                self.assertEqual(self.client.auth_token, "test_token")

    def test_verify_2fa_code_failure(self):
        mock_response = Mock()
        mock_response.status_code = 400

        with patch.object(self.client.session, "post", return_value=mock_response):
            with patch("time.sleep"):
                result = self.client._verify_2fa_code("test@example.com", "password123", "wrong_code")
                self.assertFalse(result)

    def test_build_auth_payload(self):
        payload = self.client._build_auth_payload("test@example.com", "pass123")
        self.assertEqual(payload["email"], "test@example.com")
        self.assertEqual(payload["password"], "pass123")
        self.assertEqual(payload["platform"], "web")
        self.assertIn("requested_experiments", payload)
        self.assertIn("client_id", payload)

    def test_build_auth_payload_with_extra(self):
        payload = self.client._build_auth_payload("test@example.com", "pass123", code="999999")
        self.assertEqual(payload["code"], "999999")

    # -------------------------------------------------------------------------
    # Session tests
    # -------------------------------------------------------------------------

    def test_save_session(self):
        self.client.user_info = SAMPLE_USER_DATA

        mock_cookie = Mock()
        mock_cookie.name = "session_id"
        mock_cookie.value = "abc123"
        mock_cookie.domain = "any.do"
        mock_cookie.path = "/"

        with patch.object(self.client.session, "cookies", [mock_cookie]):
            with patch("builtins.open", mock_open()) as mock_file:
                with patch("json.dump") as mock_json_dump:
                    self.client._save_session()

                    mock_file.assert_called_once_with(self.temp_session_file, "w")
                    mock_json_dump.assert_called_once()
                    call_args = mock_json_dump.call_args[0][0]
                    self.assertIn("cookies", call_args)
                    self.assertIn("user_info", call_args)

    def test_load_session(self):
        session_data = {
            "cookies": [{"name": "session_id", "value": "abc123", "domain": "any.do", "path": "/"}],
            "user_info": SAMPLE_USER_DATA,
        }

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=json.dumps(session_data))):
                with patch.object(self.client, "_test_session", return_value=True):
                    result = self.client._load_session()
                    self.assertTrue(result)
                    self.assertTrue(self.client.logged_in)
                    self.assertEqual(self.client.user_info, SAMPLE_USER_DATA)

    def test_test_session(self):
        mock_response = Mock()
        mock_response.status_code = 200
        with patch.object(self.client.session, "get", return_value=mock_response):
            self.assertTrue(self.client._test_session())

    def test_test_session_failure(self):
        mock_response = Mock()
        mock_response.status_code = 401
        with patch.object(self.client.session, "get", return_value=mock_response):
            self.assertFalse(self.client._test_session())

    def test_clear_session(self):
        self.client.user_info = SAMPLE_USER_DATA
        self.client.logged_in = True

        with patch("os.path.exists", return_value=True):
            with patch("os.remove") as mock_remove:
                self.client._clear_session()
                self.assertIsNone(self.client.user_info)
                self.assertFalse(self.client.logged_in)
                mock_remove.assert_called_once_with(self.temp_session_file)

    def test_get_user_info(self):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_USER_DATA

        with patch.object(self.client.session, "get", return_value=mock_response):
            with patch.object(self.client.session, "put", return_value=mock_response):
                self.client._get_user_info()
                self.assertEqual(self.client.user_info, SAMPLE_USER_DATA)

    def test_session_save_with_hashes(self):
        self.client.user_info = SAMPLE_USER_DATA
        self.client.last_data_hash = "test_data_hash"
        self.client.last_pretty_hash = "test_pretty_hash"

        mock_cookie = Mock()
        mock_cookie.name = "session_id"
        mock_cookie.value = "abc123"
        mock_cookie.domain = "any.do"
        mock_cookie.path = "/"

        with patch.object(self.client.session, "cookies", [mock_cookie]):
            with patch("builtins.open", mock_open()) as mock_file:
                with patch("json.dump") as mock_json_dump:
                    self.client._save_session()

                    mock_file.assert_called_once_with(self.temp_session_file, "w")
                    mock_json_dump.assert_called_once()
                    call_args = mock_json_dump.call_args[0][0]
                    self.assertEqual(call_args["last_data_hash"], "test_data_hash")
                    self.assertEqual(call_args["last_pretty_hash"], "test_pretty_hash")

    def test_session_load_with_hashes(self):
        session_data = {
            "cookies": [],
            "user_info": SAMPLE_USER_DATA,
            "last_data_hash": "loaded_data_hash",
            "last_pretty_hash": "loaded_pretty_hash",
        }

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=json.dumps(session_data))):
                with patch.object(self.client, "_test_session", return_value=True):
                    result = self.client._load_session()
                    self.assertTrue(result)
                    self.assertEqual(self.client.last_data_hash, "loaded_data_hash")
                    self.assertEqual(self.client.last_pretty_hash, "loaded_pretty_hash")

    # -------------------------------------------------------------------------
    # Sync tests
    # -------------------------------------------------------------------------

    def test_get_tasks_not_logged_in(self):
        result = self.client.get_tasks()
        self.assertIsNone(result)

    @patch("time.sleep")
    def test_get_tasks_success(self, mock_sleep):
        self.client.logged_in = True

        mock_sync_response = Mock()
        mock_sync_response.status_code = 200
        mock_sync_response.json.return_value = SAMPLE_SYNC_RESPONSE
        mock_sync_response.raise_for_status = Mock()

        mock_tasks_response = Mock()
        mock_tasks_response.status_code = 200
        mock_tasks_response.json.return_value = SAMPLE_TASKS_DATA
        mock_tasks_response.raise_for_status = Mock()

        with patch.object(self.client.session, "get", side_effect=[mock_sync_response, mock_tasks_response]):
            with patch.object(self.client, "_save_session"):
                result = self.client.get_tasks()
                self.assertEqual(result, SAMPLE_TASKS_DATA)

    def test_get_tasks_no_task_id(self):
        self.client.logged_in = True

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.raise_for_status = Mock()

        with patch.object(self.client.session, "get", return_value=mock_response):
            result = self.client.get_tasks()
            self.assertIsNone(result)

    @patch("time.sleep")
    def test_poll_for_result_success(self, mock_sleep):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_TASKS_DATA

        with patch.object(self.client.session, "get", return_value=mock_response):
            result = self.client._poll_for_result("test-task-id", max_wait=10)
            self.assertIsNotNone(result)
            self.assertEqual(result.status_code, 200)

    @patch("time.sleep")
    def test_poll_for_result_timeout(self, mock_sleep):
        mock_response = Mock()
        mock_response.status_code = 202

        with patch.object(self.client.session, "get", return_value=mock_response):
            result = self.client._poll_for_result("test-task-id", max_wait=1.0)
            self.assertIsNone(result)

    # -------------------------------------------------------------------------
    # Task data tests
    # -------------------------------------------------------------------------

    def test_get_simple_tasks(self):
        with patch.object(self.client, "get_tasks", return_value=SAMPLE_TASKS_DATA):
            result = self.client.get_simple_tasks()
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["title"], "Buy groceries")
            self.assertFalse(result[0]["completed"])
            self.assertEqual(result[1]["title"], "Complete project")
            self.assertTrue(result[1]["completed"])

    def test_get_simple_tasks_no_data(self):
        with patch.object(self.client, "get_tasks", return_value=None):
            result = self.client.get_simple_tasks()
            self.assertEqual(result, [])

    def test_get_lists(self):
        with patch.object(self.client, "get_tasks", return_value=SAMPLE_TASKS_DATA):
            result = self.client.get_lists()
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["name"], "Personal")
            self.assertTrue(result[0]["is_default"])
            self.assertEqual(result[1]["name"], "Work")
            self.assertFalse(result[1]["is_default"])

    def test_get_lists_no_data(self):
        with patch.object(self.client, "get_tasks", return_value=None):
            result = self.client.get_lists()
            self.assertEqual(result, [])

    # -------------------------------------------------------------------------
    # Task operations tests
    # -------------------------------------------------------------------------

    def test_delete_task_success(self):
        self.client.logged_in = True
        mock_response = Mock()
        mock_response.status_code = 204

        with patch.object(self.client.session, "delete", return_value=mock_response):
            self.assertTrue(self.client.delete_task("task-id-123"))

    def test_create_task_success(self):
        self.client.logged_in = True
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": "new-task-id", "title": "Buy milk"}]

        with patch.object(self.client.session, "put", return_value=mock_response):
            result = self.client.create_task("Buy milk", category_id="cat1")
            self.assertIsNotNone(result)
            self.assertEqual(result["title"], "Buy milk")

    def test_create_task_with_labels(self):
        self.client.logged_in = True
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": "new-task-id", "title": "Shoes", "labels": ["buy-label"]}]

        with patch.object(self.client.session, "put", return_value=mock_response) as mock_put:
            result = self.client.create_task("Shoes", category_id="cat1", labels=["buy-label"])
            self.assertIsNotNone(result)
            payload = mock_put.call_args[1]["json"][0]
            self.assertEqual(payload["labels"], ["buy-label"])
            self.assertIn("labelsUpdateTime", payload)

    def test_create_task_not_logged_in(self):
        self.assertIsNone(self.client.create_task("Test"))

    def test_create_task_failure(self):
        self.client.logged_in = True
        mock_response = Mock()
        mock_response.status_code = 500

        with patch.object(self.client.session, "put", return_value=mock_response):
            self.assertIsNone(self.client.create_task("Test", category_id="cat1"))

    def test_get_label_id(self):
        self.client.logged_in = True
        tasks_data = {
            "models": {
                "label": {
                    "items": [
                        {"id": "lbl1", "name": "Buy", "isDeleted": False},
                        {"id": "lbl2", "name": "Home", "isDeleted": False},
                    ]
                },
                "task": {"items": []},
                "category": {"items": []},
            }
        }
        self.assertEqual(self.client.get_label_id("Buy", tasks_data), "lbl1")
        self.assertEqual(self.client.get_label_id("buy", tasks_data), "lbl1")
        self.assertIsNone(self.client.get_label_id("Nonexistent", tasks_data))

    def test_get_category_id(self):
        self.client.logged_in = True
        self.assertEqual(self.client.get_category_id("Personal", SAMPLE_TASKS_DATA), "list1")
        self.assertEqual(self.client.get_category_id("personal", SAMPLE_TASKS_DATA), "list1")
        self.assertIsNone(self.client.get_category_id("Nonexistent", SAMPLE_TASKS_DATA))

    def test_delete_task_not_logged_in(self):
        self.assertFalse(self.client.delete_task("task-id-123"))

    def test_delete_task_failure(self):
        self.client.logged_in = True
        mock_response = Mock()
        mock_response.status_code = 500

        with patch.object(self.client.session, "delete", return_value=mock_response):
            self.assertFalse(self.client.delete_task("task-id-123"))

    def test_delete_task_network_error(self):
        self.client.logged_in = True

        with patch.object(self.client.session, "delete", side_effect=requests.RequestException("timeout")):
            self.assertFalse(self.client.delete_task("task-id-123"))

    # -------------------------------------------------------------------------
    # Change detection tests
    # -------------------------------------------------------------------------

    def test_calculate_data_hash(self):
        hash1 = self.client._calculate_data_hash(SAMPLE_TASKS_DATA)
        hash2 = self.client._calculate_data_hash(SAMPLE_TASKS_DATA)
        self.assertEqual(hash1, hash2)

        hash3 = self.client._calculate_data_hash({"different": "data"})
        self.assertNotEqual(hash1, hash3)

    def test_has_meaningful_task_data(self):
        self.assertTrue(self.client._has_meaningful_task_data(SAMPLE_TASKS_DATA))
        self.assertFalse(self.client._has_meaningful_task_data({}))
        self.assertFalse(self.client._has_meaningful_task_data({"models": {}}))

    # -------------------------------------------------------------------------
    # Export tests
    # -------------------------------------------------------------------------

    @patch("os.makedirs")
    @patch("os.path.getsize")
    def test_save_tasks_to_file(self, mock_getsize, mock_makedirs):
        mock_getsize.return_value = 1024

        with patch("builtins.open", mock_open()):
            with patch("anydown.client.datetime") as mock_datetime:
                mock_datetime.now.return_value.strftime.return_value = "2024-01-15_1430-45"

                result = self.client.save_tasks_to_file(SAMPLE_TASKS_DATA)

                expected_path = os.path.join("outputs/raw-json", "2024-01-15_1430-45_anydo-tasks.json")
                self.assertEqual(result, expected_path)

                mock_makedirs.assert_has_calls(
                    [
                        call("outputs/raw-json", exist_ok=True),
                        call("outputs/markdown", exist_ok=True),
                    ],
                    any_order=True,
                )

    def test_save_tasks_to_file_no_changes(self):
        self.client.last_data_hash = self.client._calculate_data_hash(SAMPLE_TASKS_DATA)
        result = self.client.save_tasks_to_file(SAMPLE_TASKS_DATA)
        self.assertIsNone(result)

    def test_save_tasks_to_file_no_data(self):
        result = self.client.save_tasks_to_file({})
        self.assertIsNone(result)

    @patch("os.makedirs")
    @patch("os.path.getsize")
    @patch("anydown.client.AnyDoClient._save_markdown_from_json")
    def test_save_tasks_to_file_with_markdown(self, mock_save_markdown, mock_getsize, mock_makedirs):
        mock_getsize.return_value = 1024
        mock_save_markdown.return_value = "outputs/markdown/2024-01-15_1430-45_anydo-tasks.md"

        with patch("builtins.open", mock_open()):
            with patch("anydown.client.datetime") as mock_datetime:
                mock_datetime.now.return_value.strftime.return_value = "2024-01-15_1430-45"

                result = self.client.save_tasks_to_file(SAMPLE_TASKS_DATA)
                expected_path = os.path.join("outputs/raw-json", "2024-01-15_1430-45_anydo-tasks.json")
                self.assertEqual(result, expected_path)
                mock_save_markdown.assert_called_once_with(SAMPLE_TASKS_DATA, "2024-01-15_1430-45")

    # -------------------------------------------------------------------------
    # Pretty data extraction tests
    # -------------------------------------------------------------------------

    def test_extract_pretty_data_basic(self):
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
                            "creationDate": "1640995200000",
                            "lastUpdateDate": "1640995200000",
                            "categoryId": "cat1",
                            "labels": ["work", "urgent"],
                            "note": "Important task",
                            "parentGlobalTaskId": None,
                        }
                    ]
                },
                "category": {"items": [{"id": "cat1", "name": "Work", "color": "blue", "isDefault": False}]},
            }
        }

        pretty_data = self.client._extract_pretty_data(tasks_data, verbose=False)

        self.assertEqual(pretty_data["export_info"]["total_tasks"], 1)
        self.assertEqual(pretty_data["export_info"]["pending_tasks"], 1)
        self.assertEqual(pretty_data["export_info"]["completed_tasks"], 0)

        self.assertIn("Work", pretty_data["lists"])
        self.assertNotIn("cat1", pretty_data["lists"])
        self.assertEqual(pretty_data["lists"]["Work"]["task_count"], 1)

        self.assertNotIn("color", pretty_data["lists"]["Work"])
        self.assertNotIn("is_default", pretty_data["lists"]["Work"])

        self.assertIn("Work", pretty_data["tasks"])
        task = pretty_data["tasks"]["Work"][0]

        self.assertEqual(task["title"], "Test Task")
        self.assertEqual(task["list_name"], "Work")
        self.assertEqual(task["note"], "Important task")
        self.assertEqual(task["tags"], ["work", "urgent"])
        self.assertEqual(task["created_date"], "2022-01-01 00:00")

        self.assertNotIn("status", task)
        self.assertNotIn("priority", task)
        self.assertNotIn("id", task)
        self.assertNotIn("parent_id", task)

    def test_extract_pretty_data_verbose(self):
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
                            "parentGlobalTaskId": None,
                        }
                    ]
                },
                "category": {"items": [{"id": "cat1", "name": "Work", "color": "blue", "isDefault": True}]},
            }
        }

        pretty_data = self.client._extract_pretty_data(tasks_data, verbose=True)

        self.assertIn("color", pretty_data["lists"]["Work"])
        self.assertEqual(pretty_data["lists"]["Work"]["color"], "blue")
        self.assertEqual(pretty_data["lists"]["Work"]["is_default"], True)

        task = pretty_data["tasks"]["Work"][0]
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["priority"], "high")
        self.assertEqual(task["list_color"], "blue")
        self.assertEqual(task["assignee"], "user@example.com")
        self.assertEqual(task["repeating"], "TASK_REPEAT_WEEKLY")
        self.assertEqual(task["created_date"], "2022-01-01 00:00:00")

    def test_extract_pretty_data_subtasks(self):
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
                        },
                        {
                            "id": "subtask1",
                            "globalTaskId": "subtask1",
                            "title": "Subtask 1",
                            "status": "CHECKED",
                            "categoryId": "cat1",
                            "parentGlobalTaskId": "task1",
                            "note": "First subtask",
                        },
                        {
                            "id": "subtask2",
                            "globalTaskId": "subtask2",
                            "title": "Subtask 2",
                            "status": "UNCHECKED",
                            "categoryId": "cat1",
                            "parentGlobalTaskId": "task1",
                        },
                    ]
                },
                "category": {"items": [{"id": "cat1", "name": "Personal", "color": "green", "isDefault": True}]},
            }
        }

        pretty_data = self.client._extract_pretty_data(tasks_data, verbose=False)

        self.assertEqual(len(pretty_data["tasks"]["Personal"]), 1)
        task = pretty_data["tasks"]["Personal"][0]
        self.assertEqual(task["title"], "Main Task")
        self.assertEqual(len(task["subtasks"]), 2)
        self.assertEqual(task["subtasks"][0]["title"], "Subtask 1")
        self.assertEqual(task["subtasks"][0]["note"], "First subtask")
        self.assertNotIn("note", task["subtasks"][1])

    # -------------------------------------------------------------------------
    # Display / summary tests
    # -------------------------------------------------------------------------

    def test_print_tasks_summary(self):
        """print_tasks_summary now uses logging, not print."""
        with patch.object(
            self.client,
            "get_simple_tasks",
            return_value=[
                {
                    "title": "Task 1",
                    "completed": False,
                    "priority": "HIGH",
                    "list_id": "list1",
                    "due_date": "2024-01-15",
                },
                {"title": "Task 2", "completed": True, "priority": "NORMAL", "list_id": "list2", "due_date": None},
            ],
        ):
            with patch.object(
                self.client,
                "get_lists",
                return_value=[
                    {"id": "list1", "name": "Personal"},
                    {"id": "list2", "name": "Work"},
                ],
            ):
                # Should not raise
                self.client.print_tasks_summary()

    def test_print_tasks_summary_no_tasks(self):
        with patch.object(self.client, "get_simple_tasks", return_value=[]):
            # Should not raise
            self.client.print_tasks_summary()

    # -------------------------------------------------------------------------
    # Markdown tests
    # -------------------------------------------------------------------------

    def test_save_markdown_tasks(self):
        test_data = {
            "export_info": {
                "extracted_at": "2024-01-01 12:00:00",
                "total_tasks": 3,
                "pending_tasks": 2,
                "completed_tasks": 1,
            },
            "lists": {"Test List": {"task_count": 3, "pending_count": 2, "completed_count": 1}},
            "tasks": {
                "Test List": [
                    {
                        "title": "Test Task 1",
                        "created_date": "2024-01-01 10:00",
                        "due_date": "2024-01-02 12:00",
                        "note": "Test note",
                        "_internal_status": "pending",
                    },
                    {
                        "title": "Test Task 2",
                        "created_date": "2024-01-01 10:30",
                        "due_date": "",
                        "note": "",
                        "_internal_status": "completed",
                        "subtasks": [
                            {"title": "Subtask 1", "_internal_status": "pending"},
                            {"title": "Subtask 2", "_internal_status": "completed"},
                        ],
                    },
                ]
            },
        }

        result = self.client._save_markdown_tasks(test_data, "2024-01-01_1200-00", verbose=False)
        self.assertIsNotNone(result)
        if result:
            self.assertTrue(os.path.exists(result))
            with open(result, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("# 📋 Any.do Tasks Export (Clean Mode)", content)
            self.assertIn("Test Task 1", content)
            self.assertIn("√&nbsp;&nbsp;Test Task 2", content)
            self.assertIn("&nbsp;&nbsp;&nbsp;- Subtask 1", content)
            self.assertIn("&nbsp;&nbsp;&nbsp;√&nbsp;&nbsp;Subtask 2", content)
            os.remove(result)

    def test_generate_markdown_content(self):
        test_data = {
            "export_info": {
                "extracted_at": "2024-01-01 12:00:00",
                "total_tasks": 2,
                "pending_tasks": 1,
                "completed_tasks": 1,
            },
            "lists": {"Work": {"task_count": 2, "pending_count": 1, "completed_count": 1}},
            "tasks": {
                "Work": [
                    {"title": "Meeting with team", "created_date": "2024-01-01 09:00", "_internal_status": "pending"},
                    {
                        "title": "Review code",
                        "created_date": "2024-01-01 08:00",
                        "note": "Check PR #123",
                        "_internal_status": "completed",
                    },
                ]
            },
        }

        content = self.client._generate_markdown_content(test_data, verbose=False)

        self.assertIn("# 📋 Any.do Tasks Export (Clean Mode)", content)
        self.assertIn("*Generated: 2024-01-01 12:00:00*", content)
        self.assertIn("| 📋 Total Tasks | 2 |", content)
        self.assertIn("Meeting with team", content)
        self.assertIn("√&nbsp;&nbsp;Review code", content)

    # -------------------------------------------------------------------------
    # Formatting utility tests
    # -------------------------------------------------------------------------

    def test_get_status_emoji(self):
        self.assertEqual(self.client._get_status_emoji({"_internal_status": "pending"}, verbose=False), "")
        self.assertEqual(
            self.client._get_status_emoji({"_internal_status": "completed"}, verbose=False), "√&nbsp;&nbsp;"
        )
        self.assertEqual(self.client._get_status_emoji({"status": "pending"}, verbose=True), "")
        self.assertEqual(self.client._get_status_emoji({"status": "completed"}, verbose=True), "√&nbsp;&nbsp;")

    def test_get_priority_emoji(self):
        self.assertEqual(self.client._get_priority_emoji("high"), "🔴")
        self.assertEqual(self.client._get_priority_emoji("HIGH"), "🔴")
        self.assertEqual(self.client._get_priority_emoji("medium"), "🟡")
        self.assertEqual(self.client._get_priority_emoji("low"), "🟢")
        self.assertEqual(self.client._get_priority_emoji("normal"), "🟢")

    def test_format_task_title(self):
        for title in ["Fix bug in login", "Buy groceries", "Regular task"]:
            task = {"title": title}
            self.assertEqual(self.client._format_task_title(task), title)

    def test_wrap_text(self):
        short_text = "This is a short title"
        self.assertEqual(self.client._wrap_text(short_text), short_text)

        long_text = "This is a very long task title that should definitely be wrapped at 80 characters because it exceeds the default width"
        result = self.client._wrap_text(long_text)
        for line in result.split("\n"):
            self.assertLessEqual(len(line), 80)

        result_custom = self.client._wrap_text(long_text, width=40)
        for line in result_custom.split("\n"):
            self.assertLessEqual(len(line), 40)

    def test_wrap_text_markdown_safe(self):
        text = "Line one\nLine two"
        result = self.client._wrap_text(text, markdown_safe=True)
        self.assertIn("<br>", result)
        self.assertNotIn("\n", result)

    def test_sort_tasks_for_display(self):
        tasks = [
            {"title": "Completed old", "created_date": "2024-01-01 10:00", "_internal_status": "completed"},
            {
                "title": "Pending with due",
                "created_date": "2024-01-02 10:00",
                "due_date": "2024-01-10 15:00",
                "_internal_status": "pending",
            },
            {"title": "Pending no due", "created_date": "2024-01-03 10:00", "_internal_status": "pending"},
            {"title": "Completed new", "created_date": "2024-01-04 10:00", "_internal_status": "completed"},
            {
                "title": "Pending earlier due",
                "created_date": "2024-01-01 10:00",
                "due_date": "2024-01-05 15:00",
                "_internal_status": "pending",
            },
        ]

        sorted_tasks = self.client._sort_tasks_for_display(tasks)
        actual_titles = [t["title"] for t in sorted_tasks]
        self.assertEqual(
            actual_titles,
            [
                "Pending earlier due",
                "Pending with due",
                "Pending no due",
                "Completed new",
                "Completed old",
            ],
        )


if __name__ == "__main__":
    unittest.main()
