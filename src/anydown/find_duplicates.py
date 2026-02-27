#!/usr/bin/env python3
"""
Find and optionally remove duplicate tasks in Any.do.

Duplicates are identified conservatively: two tasks are only considered
identical if they share the same title, category, parent task, note content,
and subtask signatures. Tasks that differ in any of these fields are left alone.

When --delete is used, a fresh sync is performed against the live API to ensure
duplicates are verified against current server state, not a stale local backup.

Usage:
    anydown-dupes                  # dry-run: list duplicates from latest backup
    anydown-dupes --delete         # fresh-sync, confirm, then delete via API
    anydown-dupes --keep newest    # keep the newest copy instead of oldest
    anydown-dupes --delete --yes   # skip confirmation prompt
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any

from anydown.client import AnyDoClient

logger = logging.getLogger(__name__)


def _normalise_note(note: str | None) -> str:
    """Normalise a note for comparison (strip whitespace, treat None as empty)."""
    return (note or "").strip()


def _subtask_signature(task_id: str, all_tasks: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    """
    Return a sorted tuple of (title, note, status) for each subtask of a given parent.

    Comparing notes and statuses ensures that tasks whose subtasks have been
    edited independently are not treated as duplicates.
    """
    children = []
    for t in all_tasks:
        if t.get("parentGlobalTaskId") == task_id:
            children.append(
                (
                    t.get("title", "").strip(),
                    _normalise_note(t.get("note")),
                    t.get("status", ""),
                )
            )
    return tuple(sorted(children))


def _identity_key(task: dict[str, Any], all_tasks: list[dict[str, Any]]) -> tuple:
    """
    Build a hashable key that represents a task's identity.

    Two tasks with the same key are considered duplicates.
    Components: (title, categoryId, parentGlobalTaskId, dueDate, normalised_note, subtask_signature)

    dueDate is included because repeating tasks spawn separate instances with
    different due dates — those are distinct tasks, not duplicates.
    """
    title = task.get("title", "").strip()
    category = task.get("categoryId", "")
    parent = task.get("parentGlobalTaskId")
    due_date = task.get("dueDate")
    note = _normalise_note(task.get("note"))
    task_id = task.get("id", "")
    subtasks = _subtask_signature(task_id, all_tasks)
    return (title, category, parent, due_date, note, subtasks)


def find_duplicate_groups(tasks: list[dict[str, Any]]) -> dict[tuple, list[dict[str, Any]]]:
    """
    Group tasks by identity key and return only groups with more than one member.
    Skips tasks with empty titles.
    """
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        if not task.get("title", "").strip():
            continue
        key = _identity_key(task, tasks)
        groups[key].append(task)
    return {k: v for k, v in groups.items() if len(v) > 1}


def choose_tasks_to_delete(
    group: list[dict[str, Any]], keep: str = "oldest"
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Given a group of duplicate tasks, decide which one to keep and which to delete.

    Args:
        group: list of duplicate tasks
        keep: "oldest" keeps the task with the smallest creationDate,
              "newest" keeps the one with the largest creationDate.

    Returns:
        (kept_task, tasks_to_delete)
    """
    sorted_group = sorted(group, key=lambda t: t.get("creationDate", 0))
    if keep == "newest":
        return sorted_group[-1], sorted_group[:-1]
    return sorted_group[0], sorted_group[1:]


def format_task_line(task: dict[str, Any]) -> str:
    """Format a single task for display."""
    task_id = task.get("id", "?")
    status = task.get("status", "?")
    created = task.get("creationDate")
    created_str = ""
    if created:
        try:
            created_str = datetime.fromtimestamp(created / 1000).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError, OSError):
            created_str = str(created)
    return f"    {task_id}  status={status}  created={created_str}"


def print_report(
    duplicate_groups: dict[tuple, list[dict[str, Any]]],
    keep: str,
) -> int:
    """Print a human-readable report of duplicate groups. Returns total tasks to delete."""
    if not duplicate_groups:
        print("No duplicates found.")
        return 0

    total_to_delete = 0
    for key, group in sorted(duplicate_groups.items(), key=lambda kv: kv[0][0]):
        title = key[0]
        note = key[3]
        kept, to_delete = choose_tasks_to_delete(group, keep)
        total_to_delete += len(to_delete)

        note_preview = ""
        if note:
            preview = note[:60].replace("\n", " ")
            note_preview = f'  note="{preview}{"..." if len(note) > 60 else ""}"'

        print(f'\n"{title}" ({len(group)} copies, keeping {keep}){note_preview}')
        print(f"  KEEP: {format_task_line(kept).strip()}")
        for t in to_delete:
            print(f"  DEL:  {format_task_line(t).strip()}")

    print(f"\nTotal: {len(duplicate_groups)} duplicate groups, {total_to_delete} tasks to delete")
    return total_to_delete


def delete_duplicates(
    client: AnyDoClient,
    duplicate_groups: dict[tuple, list[dict[str, Any]]],
    keep: str,
) -> tuple[int, int]:
    """
    Delete duplicate tasks via the API. Stops immediately on first failure.

    Returns:
        (deleted_count, failed_count)
    """
    deleted = 0
    for _key, group in duplicate_groups.items():
        _kept, to_delete = choose_tasks_to_delete(group, keep)
        for task in to_delete:
            task_id = task.get("id", "")
            title = task.get("title", "?")
            if not client.delete_task(task_id):
                logger.error('Failed to delete: "%s" (%s) — stopping to avoid partial state', title, task_id)
                return deleted, 1
            deleted += 1
            logger.info('Deleted: "%s" (%s)', title, task_id)
    return deleted, 0


def load_tasks_from_backup(directory: str = "outputs/raw-json") -> list[dict[str, Any]]:
    """Load tasks from the latest local JSON backup file."""
    import glob as glob_mod

    pattern = os.path.join(directory, "*.json")
    files = glob_mod.glob(pattern)
    if not files:
        raise FileNotFoundError(f"No JSON files found in {directory}")

    latest = sorted(files)[-1]
    logger.info("Loading tasks from: %s", latest)

    with open(latest, encoding="utf-8") as f:
        data = json.load(f)

    tasks = data.get("models", {}).get("task", {}).get("items", [])
    logger.info("Found %d tasks", len(tasks))
    return tasks


def load_tasks_from_api(client: AnyDoClient) -> list[dict[str, Any]]:
    """Fetch current tasks directly from the API via a full sync."""
    logger.info("Performing fresh full sync from Any.do...")
    tasks_data = client.get_tasks_full()
    if not tasks_data:
        return []
    tasks = tasks_data.get("models", {}).get("task", {}).get("items", [])
    logger.info("Fetched %d tasks from server", len(tasks))
    return tasks


def get_authenticated_client() -> AnyDoClient:
    """Create and authenticate an AnyDoClient from config.json / session."""
    session_file = os.environ.get("ANYDO_SESSION_FILE", "session.json")
    client = AnyDoClient(session_file=session_file)

    if not client.logged_in:
        try:
            with open("config.json", encoding="utf-8") as f:
                config = json.load(f)
            email = config.get("email", "")
            password = config.get("password", "")
        except (FileNotFoundError, json.JSONDecodeError):
            print("Error: config.json required for authentication")
            sys.exit(1)

        if not client.login(email, password):
            print("Login failed.")
            sys.exit(1)

    return client


def load_config_keep() -> str:
    """Read dedup_keep from config.json, defaulting to 'oldest'."""
    try:
        with open("config.json", encoding="utf-8") as f:
            config = json.load(f)
        value = config.get("dedup_keep", "oldest")
        if value in ("oldest", "newest"):
            return value
        logger.warning("Invalid dedup_keep value '%s', using 'oldest'", value)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return "oldest"


def main():
    parser = argparse.ArgumentParser(description="Find and remove duplicate Any.do tasks")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually delete duplicates via the API (default is dry-run)",
    )
    parser.add_argument(
        "--keep",
        choices=["oldest", "newest"],
        default=None,
        help="Which copy to keep (default: from config.json or 'oldest')",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt (use with --delete)",
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="Reduce logging")
    parser.add_argument("--debug", action="store_true", help="Debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.debug else (logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")

    keep = args.keep or load_config_keep()

    if not args.delete:
        # Dry-run: read from local backup, no API needed
        try:
            tasks = load_tasks_from_backup()
        except FileNotFoundError as e:
            print(f"Error: {e}")
            sys.exit(1)

        if not tasks:
            print("No tasks found.")
            return

        groups = find_duplicate_groups(tasks)
        total_to_delete = print_report(groups, keep)

        if groups:
            print(f"\nDry run — pass --delete to remove {total_to_delete} duplicate tasks.")
            print("(A fresh sync will be performed before any deletions.)")
        return

    # --delete mode: authenticate, fresh-sync, re-identify, confirm, delete
    print("Connecting to Any.do for a fresh sync before deletion...")
    client = get_authenticated_client()

    tasks = load_tasks_from_api(client)
    if not tasks:
        print("No tasks returned from server.")
        return

    groups = find_duplicate_groups(tasks)
    total_to_delete = print_report(groups, keep)

    if not groups:
        return

    if not args.yes:
        print(f"\nAbout to permanently delete {total_to_delete} tasks from your Any.do account.")
        try:
            answer = input("Type 'yes' to confirm: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return
        if answer != "yes":
            print("Cancelled.")
            return

    print(f"\nDeleting {total_to_delete} duplicate tasks...")
    deleted, failed = delete_duplicates(client, groups, keep)

    if failed:
        print(f"\nStopped after {deleted} deletions due to a failure. Re-run to retry remaining.")
    else:
        print(f"\nDone: {deleted} duplicate tasks deleted.")


if __name__ == "__main__":
    main()
