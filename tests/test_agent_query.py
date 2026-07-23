"""Tests for agent export query filtering."""

import unittest

from anydown.agent_query import filter_agent_export

SAMPLE_EXPORT = {
    "exported_at": "2026-07-23 00:00:00",
    "pending_tasks": 4,
    "lists": [{"id": "cat1", "name": "Personal"}],
    "tags": [{"id": "lbl1", "name": "Buy"}],
    "tasks": [
        {
            "id": "t1",
            "title": "Zebra task",
            "list": "Personal",
            "tags": ["Buy"],
            "creation_ms": 3000,
            "due_ms": 2000,
        },
        {
            "id": "t2",
            "title": "Alpha task",
            "list": "Work",
            "tags": ["Urgent"],
            "creation_ms": 1000,
        },
        {
            "id": "t3",
            "title": "Middle task",
            "list": "Personal",
            "creation_ms": 2000,
            "due_ms": 1000,
        },
        {
            "id": "t4",
            "title": "Buy milk",
            "list": "Grocery",
            "tags": ["Buy"],
            "creation_ms": 4000,
            "due_ms": 3000,
            "note": "2%",
        },
    ],
}


class TestFilterAgentExport(unittest.TestCase):
    def test_default_title_sort(self):
        result = filter_agent_export(SAMPLE_EXPORT, {})
        self.assertEqual([task["id"] for task in result["tasks"]], ["t2", "t4", "t3", "t1"])
        self.assertEqual(result["matched_tasks"], 4)
        self.assertEqual(result["returned_tasks"], 4)
        self.assertIn("lists", result)

    def test_sort_by_creation_asc(self):
        result = filter_agent_export(SAMPLE_EXPORT, {"sort": ["creation"], "order": ["asc"]})
        self.assertEqual([task["id"] for task in result["tasks"]], ["t2", "t3", "t1", "t4"])

    def test_sort_by_due_desc(self):
        result = filter_agent_export(SAMPLE_EXPORT, {"sort": ["due"], "order": ["desc"]})
        self.assertEqual([task["id"] for task in result["tasks"]], ["t4", "t1", "t3", "t2"])

    def test_limit_and_offset(self):
        result = filter_agent_export(
            SAMPLE_EXPORT,
            {"sort": ["creation"], "order": ["asc"], "limit": ["2"], "offset": ["1"]},
        )
        self.assertEqual([task["id"] for task in result["tasks"]], ["t3", "t1"])
        self.assertEqual(result["limit"], 2)
        self.assertEqual(result["offset"], 1)

    def test_filter_by_list(self):
        result = filter_agent_export(SAMPLE_EXPORT, {"list": ["person"]})
        self.assertEqual(result["matched_tasks"], 2)
        self.assertEqual(result["filters"], ["list"])

    def test_filter_by_tag(self):
        result = filter_agent_export(SAMPLE_EXPORT, {"tag": ["buy"]})
        self.assertEqual(result["matched_tasks"], 2)

    def test_filter_by_q(self):
        result = filter_agent_export(SAMPLE_EXPORT, {"q": ["2%"]})
        self.assertEqual(result["matched_tasks"], 1)
        self.assertEqual(result["tasks"][0]["id"], "t4")

    def test_has_due_and_no_due(self):
        with_due = filter_agent_export(SAMPLE_EXPORT, {"has_due": ["1"]})
        without_due = filter_agent_export(SAMPLE_EXPORT, {"no_due": ["1"]})
        self.assertEqual(with_due["matched_tasks"], 3)
        self.assertEqual(without_due["matched_tasks"], 1)

    def test_meta_minimal(self):
        result = filter_agent_export(SAMPLE_EXPORT, {"meta": ["minimal"], "limit": ["1"]})
        self.assertNotIn("lists", result)
        self.assertNotIn("tags", result)
        self.assertEqual(result["returned_tasks"], 1)
