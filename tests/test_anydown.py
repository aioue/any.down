#!/usr/bin/env python3
"""
Unit tests for the anydown.py CLI script.

Run with: pytest tests/test_anydown.py -v
"""

import unittest
from unittest.mock import Mock, mock_open, patch

from anydown.cli import get_credentials, load_config, main


class TestMainFunction(unittest.TestCase):
    """Test cases for the main script flow."""

    def setUp(self):
        self.sample_tasks_data = {
            "models": {
                "task": {
                    "items": [
                        {
                            "id": "task1",
                            "title": "Test Task",
                            "status": "UNCHECKED",
                            "priority": "NORMAL",
                            "categoryId": "list1",
                        }
                    ]
                }
            }
        }

    @patch("sys.argv", ["anydown.py"])
    @patch("anydown.cli.load_config", return_value=None)
    @patch("builtins.input", side_effect=["test@example.com", "n"])
    @patch("getpass.getpass", return_value="password123")
    @patch("anydown.cli.AnyDoClient")
    def test_main_successful_login(self, mock_client_class, mock_getpass, mock_input, mock_load_config):
        mock_client = Mock()
        mock_client.login.return_value = True
        mock_client.logged_in = True
        mock_client.last_sync_timestamp = None
        mock_client.print_tasks_summary.return_value = None
        mock_client_class.return_value = mock_client

        main()

        mock_client.login.assert_called_once_with("test@example.com", "password123")
        mock_client.print_tasks_summary.assert_called_once()

    @patch("sys.argv", ["anydown.py"])
    @patch("anydown.cli.load_config", return_value=None)
    @patch("builtins.input", return_value="test@example.com")
    @patch("getpass.getpass", return_value="wrongpassword")
    @patch("anydown.cli.AnyDoClient")
    def test_main_failed_login(self, mock_client_class, mock_getpass, mock_input, mock_load_config):
        mock_client = Mock()
        mock_client.login.return_value = False
        mock_client_class.return_value = mock_client

        main()

        mock_client.login.assert_called_once_with("test@example.com", "wrongpassword")
        mock_client.print_tasks_summary.assert_not_called()

    @patch("sys.argv", ["anydown.py"])
    @patch("anydown.cli.load_config", return_value=None)
    @patch("builtins.input", side_effect=["test@example.com", "y"])
    @patch("getpass.getpass", return_value="password123")
    @patch("anydown.cli.AnyDoClient")
    def test_main_save_raw_data(self, mock_client_class, mock_getpass, mock_input, mock_load_config):
        mock_client = Mock()
        mock_client.login.return_value = True
        mock_client.logged_in = True
        mock_client.last_sync_timestamp = None
        mock_client.get_tasks.return_value = self.sample_tasks_data
        mock_client.save_tasks_to_file.return_value = "outputs/raw-json/2024-01-15_1430-45_anydo-tasks.json"
        mock_client_class.return_value = mock_client

        main()

        mock_client.save_tasks_to_file.assert_called_once_with(self.sample_tasks_data)

    @patch("sys.argv", ["anydown.py"])
    @patch("anydown.cli.load_config", return_value=None)
    @patch("builtins.input", side_effect=["test@example.com", "y"])
    @patch("getpass.getpass", return_value="password123")
    @patch("anydown.cli.AnyDoClient")
    def test_main_no_tasks(self, mock_client_class, mock_getpass, mock_input, mock_load_config):
        mock_client = Mock()
        mock_client.login.return_value = True
        mock_client.logged_in = True
        mock_client.get_tasks.return_value = None
        mock_client_class.return_value = mock_client

        main()

        mock_client.get_tasks.assert_called_once()

    @patch("sys.argv", ["anydown.py"])
    @patch("anydown.cli.load_config", return_value=None)
    @patch("builtins.input", side_effect=["test@example.com", "n"])
    @patch("getpass.getpass", return_value="password123")
    @patch("anydown.cli.AnyDoClient")
    def test_main_no_save(self, mock_client_class, mock_getpass, mock_input, mock_load_config):
        mock_client = Mock()
        mock_client.login.return_value = True
        mock_client.logged_in = True
        mock_client.last_sync_timestamp = None
        mock_client.get_tasks.return_value = self.sample_tasks_data
        mock_client_class.return_value = mock_client

        main()

        mock_client.get_tasks.assert_called_once()
        mock_client.print_tasks_summary.assert_called_once()


class TestConfigurationHandling(unittest.TestCase):
    """Test cases for configuration file handling."""

    def setUp(self):
        self.sample_config = {
            "email": "test@example.com",
            "password": "testpassword",
            "save_raw_data": True,
            "auto_export": True,
        }

    @patch("os.path.exists", return_value=True)
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data='{"email": "test@example.com", "password": "testpassword", "save_raw_data": true, "auto_export": true}',
    )
    def test_load_config_success(self, mock_file, mock_exists):
        config = load_config()
        self.assertIsNotNone(config)
        if config:
            self.assertEqual(config["email"], "test@example.com")
            self.assertTrue(config["save_raw_data"])

    @patch("os.path.exists", return_value=False)
    def test_load_config_no_file(self, mock_exists):
        self.assertIsNone(load_config())

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data="invalid json")
    def test_load_config_invalid_json(self, mock_file, mock_exists):
        self.assertIsNone(load_config())

    @patch("anydown.cli.load_config")
    def test_get_credentials_from_config(self, mock_load_config):
        mock_load_config.return_value = self.sample_config
        email, password, save_raw, auto_export, text_wrap_width, rotate_client_id = get_credentials()

        self.assertEqual(email, "test@example.com")
        self.assertEqual(password, "testpassword")
        self.assertTrue(save_raw)
        self.assertTrue(auto_export)
        self.assertEqual(text_wrap_width, 80)
        self.assertFalse(rotate_client_id)

    @patch("anydown.cli.load_config", return_value=None)
    @patch("builtins.input", side_effect=["interactive@example.com", "y"])
    @patch("getpass.getpass", return_value="interactivepassword")
    def test_get_credentials_interactive(self, mock_getpass, mock_input, mock_load_config):
        email, password, save_raw, auto_export, text_wrap_width, rotate_client_id = get_credentials()

        self.assertEqual(email, "interactive@example.com")
        self.assertEqual(password, "interactivepassword")
        self.assertTrue(save_raw)
        self.assertEqual(text_wrap_width, 80)
        self.assertFalse(rotate_client_id)

    @patch.dict("os.environ", {"ANYDO_EMAIL": "env@example.com", "ANYDO_PASSWORD": "envpass"})
    def test_get_credentials_from_env(self):
        email, password, save_raw, auto_export, text_wrap_width, rotate_client_id = get_credentials()
        self.assertEqual(email, "env@example.com")
        self.assertEqual(password, "envpass")
        self.assertFalse(rotate_client_id)


class TestScriptIntegration(unittest.TestCase):
    def test_package_can_be_imported(self):
        import anydown

        self.assertTrue(hasattr(anydown, "AnyDoClient"))

    def test_cli_has_main_guard(self):
        from pathlib import Path

        cli_path = Path(__file__).resolve().parent.parent / "src" / "anydown" / "cli.py"
        content = cli_path.read_text()
        self.assertIn('if __name__ == "__main__":', content)
        self.assertIn("main()", content)


if __name__ == "__main__":
    unittest.main()
