"""Tests for no_create_duplicate task creation."""

import unittest
from unittest.mock import Mock, patch

from anydown.client import AnyDoClient


class TestCreateTaskNoDuplicate(unittest.TestCase):
    def setUp(self) -> None:
        self.client = AnyDoClient(session_file="/dev/null")
        self.client.logged_in = True

    def test_matching_tasks_sorted_by_position(self) -> None:
        tasks_data = {
            "models": {
                "task": {
                    "items": [
                        {
                            "globalTaskId": "b",
                            "title": "\U0001f5e3 Kitchen beeping",
                            "status": "UNCHECKED",
                            "categoryId": "cat1",
                            "position": "6111",
                        },
                        {
                            "globalTaskId": "a",
                            "title": "Kitchen beeping",
                            "status": "UNCHECKED",
                            "categoryId": "cat1",
                            "position": "610f",
                        },
                    ]
                }
            }
        }
        matches = self.client._tasks_matching_title(
            "\U0001f5e3 Kitchen beeping",
            category_id="cat1",
            tasks_data=tasks_data,
        )
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0].get("globalTaskId"), "a")

    @patch.object(AnyDoClient, "delete_task", return_value=True)
    def test_consolidate_keeps_topmost(self, _delete: Mock) -> None:
        keep = {"globalTaskId": "a", "position": "610f"}
        extra = {"globalTaskId": "b", "position": "6111"}
        result = self.client._consolidate_duplicate_active_tasks(
            [keep, extra],
            tasks_data={"models": {"task": {"items": [keep, extra]}}},
        )
        self.assertIs(result, keep)
        _delete.assert_called_once()

    def test_is_first_in_category(self) -> None:
        tasks_data = {
            "models": {
                "task": {
                    "items": [
                        {"globalTaskId": "top", "status": "UNCHECKED", "categoryId": "c", "position": "100"},
                        {"globalTaskId": "other", "status": "UNCHECKED", "categoryId": "c", "position": "200"},
                    ]
                }
            }
        }
        self.assertTrue(self.client._task_is_first_in_category("top", "c", tasks_data))
        self.assertFalse(self.client._task_is_first_in_category("other", "c", tasks_data))
