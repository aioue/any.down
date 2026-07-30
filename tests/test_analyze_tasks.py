"""Tests for deterministic task hygiene analysis."""

import unittest

from anydown.analyze_tasks import (
    analyze_export,
    find_fuzzy_title_pairs,
    find_missing_bracket_suffix,
    find_normalized_title_collisions,
    find_substring_title_pairs,
)

SAMPLE_EXPORT = {
    "exported_at": "2026-01-01 00:00:00",
    "pending_tasks": 4,
    "tasks": [
        {"id": "a1", "title": "Asda plates", "list": "Personal", "subtasks": []},
        {"id": "a2", "title": "Asda plates+bowls", "list": "Purchase", "subtasks": []},
        {"id": "b1", "title": "Theory test", "list": "Personal", "subtasks": [{"id": "s1", "title": "One"}] * 3},
        {"id": "c1", "title": "TODO", "list": "Personal", "subtasks": []},
    ],
}


class TestAnalyzeTasks(unittest.TestCase):
    def test_substring_pairs_detect_asda(self):
        pairs = find_substring_title_pairs(SAMPLE_EXPORT["tasks"])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["left"]["id"], "a1")

    def test_missing_bracket_suffix(self):
        findings = find_missing_bracket_suffix(SAMPLE_EXPORT["tasks"])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["id"], "b1")

    def test_normalized_collisions_empty_for_distinct_titles(self):
        self.assertEqual(find_normalized_title_collisions(SAMPLE_EXPORT["tasks"]), [])

    def test_fuzzy_pairs_include_asda(self):
        pairs = find_fuzzy_title_pairs(SAMPLE_EXPORT["tasks"], threshold=0.75)
        self.assertTrue(any("Asda" in pair["left"]["title"] for pair in pairs))

    def test_analyze_export_shape(self):
        report = analyze_export(SAMPLE_EXPORT, raw_json_path="/nonexistent/raw.json")
        self.assertIn("fuzzy_title_pairs", report)
        self.assertIn("weak_titles", report)
        self.assertEqual(report["pending_tasks"], 4)


if __name__ == "__main__":
    unittest.main()
