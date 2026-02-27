#!/usr/bin/env python3
"""
Unit tests for the find_duplicates module.

Run with: pytest tests/test_find_duplicates.py -v
"""

import unittest
from typing import Any
from unittest.mock import Mock

from anydown.find_duplicates import (
    _identity_key,
    _normalise_note,
    _subtask_signature,
    choose_tasks_to_delete,
    delete_duplicates,
    find_duplicate_groups,
)


def _task(
    task_id: str = "t1",
    title: str = "Bob",
    category: str = "cat1",
    note: str = "",
    parent: str | None = None,
    status: str = "UNCHECKED",
    created: int = 1000,
    due_date: int | None = None,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": title,
        "categoryId": category,
        "note": note,
        "parentGlobalTaskId": parent,
        "status": status,
        "creationDate": created,
        "dueDate": due_date,
    }


class TestNormaliseNote(unittest.TestCase):
    def test_none(self):
        self.assertEqual(_normalise_note(None), "")

    def test_empty(self):
        self.assertEqual(_normalise_note(""), "")

    def test_whitespace(self):
        self.assertEqual(_normalise_note("  \n  "), "")

    def test_content(self):
        self.assertEqual(_normalise_note("  hello  "), "hello")


class TestSubtaskSignature(unittest.TestCase):
    def test_no_subtasks(self):
        tasks = [_task("t1", "Parent")]
        self.assertEqual(_subtask_signature("t1", tasks), ())

    def test_with_subtasks(self):
        tasks = [
            _task("parent", "Parent"),
            _task("c1", "Bravo", parent="parent"),
            _task("c2", "Alpha", parent="parent"),
        ]
        sig = _subtask_signature("parent", tasks)
        self.assertEqual(len(sig), 2)
        self.assertEqual(sig[0][0], "Alpha")
        self.assertEqual(sig[1][0], "Bravo")

    def test_subtasks_of_different_parent_ignored(self):
        tasks = [
            _task("p1", "Parent1"),
            _task("p2", "Parent2"),
            _task("c1", "Child", parent="p1"),
            _task("c2", "Other", parent="p2"),
        ]
        sig = _subtask_signature("p1", tasks)
        self.assertEqual(len(sig), 1)
        self.assertEqual(sig[0][0], "Child")

    def test_subtasks_with_different_notes_differ(self):
        tasks = [
            _task("p1", "Parent"),
            _task("p2", "Parent"),
            _task("c1", "Sub", note="note A", parent="p1"),
            _task("c2", "Sub", note="note B", parent="p2"),
        ]
        self.assertNotEqual(
            _subtask_signature("p1", tasks),
            _subtask_signature("p2", tasks),
        )

    def test_subtasks_with_different_statuses_differ(self):
        tasks = [
            _task("p1", "Parent"),
            _task("p2", "Parent"),
            _task("c1", "Sub", status="UNCHECKED", parent="p1"),
            _task("c2", "Sub", status="CHECKED", parent="p2"),
        ]
        self.assertNotEqual(
            _subtask_signature("p1", tasks),
            _subtask_signature("p2", tasks),
        )


class TestIdentityKey(unittest.TestCase):
    def test_same_tasks_same_key(self):
        t1 = _task("a", "Buy milk", "cat1", "")
        t2 = _task("b", "Buy milk", "cat1", "")
        tasks = [t1, t2]
        self.assertEqual(_identity_key(t1, tasks), _identity_key(t2, tasks))

    def test_different_notes_different_key(self):
        t1 = _task("a", "Buy milk", "cat1", "whole milk")
        t2 = _task("b", "Buy milk", "cat1", "skim milk")
        tasks = [t1, t2]
        self.assertNotEqual(_identity_key(t1, tasks), _identity_key(t2, tasks))

    def test_different_categories_different_key(self):
        t1 = _task("a", "Buy milk", "cat1")
        t2 = _task("b", "Buy milk", "cat2")
        tasks = [t1, t2]
        self.assertNotEqual(_identity_key(t1, tasks), _identity_key(t2, tasks))

    def test_different_parents_different_key(self):
        t1 = _task("a", "Subtask", parent="p1")
        t2 = _task("b", "Subtask", parent="p2")
        tasks = [t1, t2]
        self.assertNotEqual(_identity_key(t1, tasks), _identity_key(t2, tasks))

    def test_subtask_vs_toplevel_different_key(self):
        t1 = _task("a", "Task")
        t2 = _task("b", "Task", parent="p1")
        tasks = [t1, t2]
        self.assertNotEqual(_identity_key(t1, tasks), _identity_key(t2, tasks))

    def test_different_subtask_children_different_key(self):
        parent1 = _task("p1", "Parent", "cat1")
        parent2 = _task("p2", "Parent", "cat1")
        child1 = _task("c1", "Sub A", parent="p1")
        child2 = _task("c2", "Sub B", parent="p2")
        tasks = [parent1, parent2, child1, child2]
        self.assertNotEqual(_identity_key(parent1, tasks), _identity_key(parent2, tasks))

    def test_note_none_vs_empty_treated_same(self):
        t1 = _task("a", "Task", note="")
        t2 = _task("b", "Task")
        t2["note"] = None
        tasks = [t1, t2]
        self.assertEqual(_identity_key(t1, tasks), _identity_key(t2, tasks))

    def test_different_due_dates_different_key(self):
        t1 = _task("a", "Check oil", due_date=1000000)
        t2 = _task("b", "Check oil", due_date=2000000)
        tasks = [t1, t2]
        self.assertNotEqual(_identity_key(t1, tasks), _identity_key(t2, tasks))

    def test_same_due_dates_same_key(self):
        t1 = _task("a", "Check oil", due_date=1000000)
        t2 = _task("b", "Check oil", due_date=1000000)
        tasks = [t1, t2]
        self.assertEqual(_identity_key(t1, tasks), _identity_key(t2, tasks))

    def test_no_due_date_same_key(self):
        t1 = _task("a", "Buy milk")
        t2 = _task("b", "Buy milk")
        tasks = [t1, t2]
        self.assertEqual(_identity_key(t1, tasks), _identity_key(t2, tasks))


class TestFindDuplicateGroups(unittest.TestCase):
    def test_no_duplicates(self):
        tasks = [_task("a", "Task A"), _task("b", "Task B")]
        self.assertEqual(find_duplicate_groups(tasks), {})

    def test_exact_duplicates(self):
        tasks = [
            _task("a", "Bob", created=100),
            _task("b", "Bob", created=200),
            _task("c", "Bob", created=300),
        ]
        groups = find_duplicate_groups(tasks)
        self.assertEqual(len(groups), 1)
        group = list(groups.values())[0]
        self.assertEqual(len(group), 3)

    def test_different_notes_not_duplicates(self):
        tasks = [
            _task("a", "Bob", note="note1"),
            _task("b", "Bob", note="note2"),
        ]
        self.assertEqual(find_duplicate_groups(tasks), {})

    def test_different_categories_not_duplicates(self):
        tasks = [
            _task("a", "Bob", category="c1"),
            _task("b", "Bob", category="c2"),
        ]
        self.assertEqual(find_duplicate_groups(tasks), {})

    def test_subtask_of_different_parents_not_duplicates(self):
        tasks = [
            _task("a", "Child", parent="p1"),
            _task("b", "Child", parent="p2"),
        ]
        self.assertEqual(find_duplicate_groups(tasks), {})

    def test_empty_titles_skipped(self):
        tasks = [_task("a", ""), _task("b", "")]
        self.assertEqual(find_duplicate_groups(tasks), {})

    def test_whitespace_titles_skipped(self):
        tasks = [_task("a", "  "), _task("b", "  ")]
        self.assertEqual(find_duplicate_groups(tasks), {})

    def test_mixed_status_still_duplicates(self):
        """Tasks with same identity but different status are still duplicates."""
        tasks = [
            _task("a", "Bob", status="UNCHECKED"),
            _task("b", "Bob", status="CHECKED"),
        ]
        groups = find_duplicate_groups(tasks)
        self.assertEqual(len(groups), 1)

    def test_different_due_dates_not_duplicates(self):
        """Repeating task instances have different due dates — not duplicates."""
        tasks = [
            _task("a", "Check oil", due_date=1000000),
            _task("b", "Check oil", due_date=2000000),
            _task("c", "Check oil", due_date=3000000),
        ]
        self.assertEqual(find_duplicate_groups(tasks), {})

    def test_same_due_dates_are_duplicates(self):
        tasks = [
            _task("a", "Check oil", due_date=1000000, created=100),
            _task("b", "Check oil", due_date=1000000, created=200),
        ]
        groups = find_duplicate_groups(tasks)
        self.assertEqual(len(groups), 1)

    def test_parents_with_different_subtask_notes_not_duplicates(self):
        tasks = [
            _task("p1", "Parent"),
            _task("p2", "Parent"),
            _task("c1", "Sub", note="note A", parent="p1"),
            _task("c2", "Sub", note="note B", parent="p2"),
        ]
        self.assertEqual(find_duplicate_groups(tasks), {})

    def test_parents_with_different_subtask_statuses_not_duplicates(self):
        tasks = [
            _task("p1", "Parent"),
            _task("p2", "Parent"),
            _task("c1", "Sub", status="UNCHECKED", parent="p1"),
            _task("c2", "Sub", status="CHECKED", parent="p2"),
        ]
        self.assertEqual(find_duplicate_groups(tasks), {})


class TestChooseTasksToDelete(unittest.TestCase):
    def test_keep_oldest(self):
        tasks = [
            _task("b", "Bob", created=200),
            _task("a", "Bob", created=100),
            _task("c", "Bob", created=300),
        ]
        kept, to_delete = choose_tasks_to_delete(tasks, keep="oldest")
        self.assertEqual(kept["id"], "a")
        self.assertEqual(len(to_delete), 2)
        self.assertEqual({t["id"] for t in to_delete}, {"b", "c"})

    def test_keep_newest(self):
        tasks = [
            _task("b", "Bob", created=200),
            _task("a", "Bob", created=100),
            _task("c", "Bob", created=300),
        ]
        kept, to_delete = choose_tasks_to_delete(tasks, keep="newest")
        self.assertEqual(kept["id"], "c")
        self.assertEqual(len(to_delete), 2)
        self.assertEqual({t["id"] for t in to_delete}, {"a", "b"})

    def test_two_items_keep_oldest(self):
        tasks = [_task("old", created=100), _task("new", created=200)]
        kept, to_delete = choose_tasks_to_delete(tasks, keep="oldest")
        self.assertEqual(kept["id"], "old")
        self.assertEqual(len(to_delete), 1)
        self.assertEqual(to_delete[0]["id"], "new")


class TestDeleteDuplicates(unittest.TestCase):
    def _make_groups(self):
        tasks_a = [_task("a1", "Dup A", created=100), _task("a2", "Dup A", created=200)]
        tasks_b = [_task("b1", "Dup B", created=100), _task("b2", "Dup B", created=200)]
        groups = find_duplicate_groups(tasks_a + tasks_b)
        return groups

    def test_all_succeed(self):
        groups = self._make_groups()
        client = Mock()
        client.delete_task.return_value = True

        deleted, failed = delete_duplicates(client, groups, "oldest")
        self.assertEqual(deleted, 2)
        self.assertEqual(failed, 0)

    def test_stops_on_first_failure(self):
        groups = self._make_groups()
        client = Mock()
        client.delete_task.side_effect = [True, False]

        deleted, failed = delete_duplicates(client, groups, "oldest")
        self.assertEqual(deleted, 1)
        self.assertEqual(failed, 1)
        self.assertEqual(client.delete_task.call_count, 2)

    def test_first_call_fails(self):
        groups = self._make_groups()
        client = Mock()
        client.delete_task.return_value = False

        deleted, failed = delete_duplicates(client, groups, "oldest")
        self.assertEqual(deleted, 0)
        self.assertEqual(failed, 1)
        self.assertEqual(client.delete_task.call_count, 1)


if __name__ == "__main__":
    unittest.main()
