#!/usr/bin/env python3
"""
Unit tests for the anydown.py script.

Run with: python -m pytest test_anydown.py -v
"""

import unittest
from unittest.mock import Mock, patch, mock_open
import json
import os
import sys

# Add the current directory to the path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from anydown import main, load_config, get_credentials


class TestGetMyTasksScript(unittest.TestCase):
    """Test cases for the main script functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.sample_tasks_data = {
            "models": {
                "task": {
                    "items": [
                        {
                            "id": "task1",
                            "title": "Test Task",
                            "status": "UNCHECKED",
                            "priority": "NORMAL",
                            "categoryId": "list1"
                        }
                    ]
                }
            }
        }
    
    @patch('sys.argv', ['anydown.py'])
    @patch('anydown.load_config', return_value=None)
    @patch('builtins.input', side_effect=['test@example.com', 'n'])
    @patch('getpass.getpass', return_value='password123')
    @patch('anydown.AnyDoClient')
    @patch('builtins.print')
    def test_main_successful_login(self, mock_print, mock_client_class, mock_getpass, mock_input, mock_load_config):
        """Test successful login and task display."""
        # Mock the client instance
        mock_client = Mock()
        mock_client.login.return_value = True
        mock_client.logged_in = True
        mock_client.print_tasks_summary.return_value = None
        mock_client_class.return_value = mock_client
        
        # Run the main function
        main()
        
        # Verify login was called with correct credentials
        mock_client.login.assert_called_once_with('test@example.com', 'password123')
        
        # Verify tasks summary was printed
        mock_client.print_tasks_summary.assert_called_once()
        
        # Verify success message was printed
        mock_print.assert_any_call("✅ Authentication successful!")
    
    @patch('sys.argv', ['anydown.py'])
    @patch('anydown.load_config', return_value=None)
    @patch('builtins.input', return_value='test@example.com')
    @patch('getpass.getpass', return_value='wrongpassword')
    @patch('anydown.AnyDoClient')
    @patch('builtins.print')
    def test_main_failed_login(self, mock_print, mock_client_class, mock_getpass, mock_input, mock_load_config):
        """Test failed login."""
        # Mock the client instance
        mock_client = Mock()
        mock_client.login.return_value = False
        mock_client_class.return_value = mock_client
        
        # Run the main function
        main()
        
        # Verify login was attempted
        mock_client.login.assert_called_once_with('test@example.com', 'wrongpassword')
        
        # Verify tasks summary was NOT called
        mock_client.print_tasks_summary.assert_not_called()
        
        # Verify error message was printed
        mock_print.assert_any_call("❌ Login failed. Please check your credentials and try again.")
    
    @patch('sys.argv', ['anydown.py'])
    @patch('anydown.load_config', return_value=None)
    @patch('builtins.input', side_effect=['test@example.com', 'y'])
    @patch('getpass.getpass', return_value='password123')
    @patch('anydown.AnyDoClient')
    @patch('builtins.print')
    def test_main_save_raw_data(self, mock_print, mock_client_class, mock_getpass, mock_input, mock_load_config):
        """Test saving raw task data to file."""
        # Mock the client instance
        mock_client = Mock()
        mock_client.login.return_value = True
        mock_client.logged_in = True
        mock_client.get_tasks.return_value = self.sample_tasks_data
        mock_client.save_tasks_to_file.return_value = "outputs/raw-json/2024-01-15_1430-45_anydo-tasks.json"
        mock_client_class.return_value = mock_client
        
        # Run the main function
        main()
        
        # Verify save_tasks_to_file was called
        mock_client.save_tasks_to_file.assert_called_once_with(self.sample_tasks_data, force=False, verbose_yaml=False)
        
        # Verify success message was printed
        mock_print.assert_any_call("✅ Tasks saved successfully")
    
    @patch('sys.argv', ['anydown.py'])
    @patch('anydown.load_config', return_value=None)
    @patch('builtins.input', side_effect=['test@example.com', 'y'])
    @patch('getpass.getpass', return_value='password123')
    @patch('anydown.AnyDoClient')
    @patch('builtins.print')
    def test_main_save_raw_data_no_tasks(self, mock_print, mock_client_class, mock_getpass, mock_input, mock_load_config):
        """Test attempting to save when no tasks are available."""
        # Mock the client instance
        mock_client = Mock()
        mock_client.login.return_value = True
        mock_client.logged_in = True
        mock_client.get_tasks.return_value = None  # No tasks available
        mock_client_class.return_value = mock_client
        
        # Run the main function
        main()
        
        # Verify that get_tasks was called
        mock_client.get_tasks.assert_called_once()
        
        # Verify error message was printed
        mock_print.assert_any_call("❌ Failed to fetch tasks. Please try again.")
    
    @patch('sys.argv', ['anydown.py'])
    @patch('anydown.load_config', return_value=None)
    @patch('builtins.input', side_effect=['test@example.com', 'n'])
    @patch('getpass.getpass', return_value='password123')
    @patch('anydown.AnyDoClient')
    @patch('builtins.print')
    def test_main_no_save_raw_data(self, mock_print, mock_client_class, mock_getpass, mock_input, mock_load_config):
        """Test not saving raw task data."""
        # Mock the client instance
        mock_client = Mock()
        mock_client.login.return_value = True
        mock_client.logged_in = True
        mock_client.get_tasks.return_value = self.sample_tasks_data
        mock_client_class.return_value = mock_client
        
        # Run the main function
        main()
        
        # Verify get_tasks was called (needed for print_tasks_summary)
        mock_client.get_tasks.assert_called_once()
        
        # Verify print_tasks_summary was called
        mock_client.print_tasks_summary.assert_called_once()


class TestConfigurationHandling(unittest.TestCase):
    """Test cases for configuration file handling."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.sample_config = {
            "email": "test@example.com",
            "password": "testpassword",
            "save_raw_data": True,
            "auto_export": True
        }
    
    @patch('os.path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data='{"email": "test@example.com", "password": "testpassword", "save_raw_data": true, "auto_export": true}')
    def test_load_config_success(self, mock_file, mock_exists):
        """Test successful configuration loading."""
        config = load_config()
        
        self.assertIsNotNone(config)
        if config:  # Type guard to satisfy linter
            self.assertEqual(config['email'], 'test@example.com')
            self.assertEqual(config['password'], 'testpassword')
            self.assertTrue(config['save_raw_data'])
            self.assertTrue(config['auto_export'])
    
    @patch('os.path.exists', return_value=False)
    def test_load_config_no_file(self, mock_exists):
        """Test configuration loading when file doesn't exist."""
        config = load_config()
        self.assertIsNone(config)
    
    @patch('os.path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data='invalid json')
    def test_load_config_invalid_json(self, mock_file, mock_exists):
        """Test configuration loading with invalid JSON."""
        config = load_config()
        self.assertIsNone(config)
    
    @patch('anydown.load_config')
    def test_get_credentials_from_config(self, mock_load_config):
        """Test getting credentials from config file."""
        mock_load_config.return_value = self.sample_config
        
        email, password, save_raw, auto_export, text_wrap_width = get_credentials()
        
        self.assertEqual(email, 'test@example.com')
        self.assertEqual(password, 'testpassword')
        self.assertTrue(save_raw)
        self.assertTrue(auto_export)
        self.assertEqual(text_wrap_width, 80)  # Default value
    
    @patch('anydown.load_config', return_value=None)
    @patch('builtins.input', side_effect=['interactive@example.com', 'y'])
    @patch('getpass.getpass', return_value='interactivepassword')
    def test_get_credentials_interactive(self, mock_getpass, mock_input, mock_load_config):
        """Test getting credentials through interactive input."""
        email, password, save_raw, auto_export, text_wrap_width = get_credentials()
        
        self.assertEqual(email, 'interactive@example.com')
        self.assertEqual(password, 'interactivepassword')
        self.assertTrue(save_raw)
        self.assertTrue(auto_export)
        self.assertEqual(text_wrap_width, 80)  # Default value


class TestScriptIntegration(unittest.TestCase):
    """Integration tests for the script."""
    
    @patch('sys.argv', ['anydown.py'])
    def test_script_can_be_imported(self):
        """Test that the script can be imported without errors."""
        try:
            import anydown
            self.assertTrue(hasattr(anydown, 'main'))
        except ImportError as e:
            self.fail(f"Failed to import anydown: {e}")
    
    def test_script_has_main_guard(self):
        """Test that the script has proper main guard."""
        with open('anydown.py', 'r') as f:
            content = f.read()
            self.assertIn('if __name__ == "__main__":', content)
            self.assertIn('main()', content)


if __name__ == '__main__':
    unittest.main() 
