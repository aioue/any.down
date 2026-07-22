#!/usr/bin/env python3
"""Slow, read-only smoke test for extended AnyDoClient capabilities."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from anydown import AnyDoClient

PAUSE_SEC = 2


def pause(label: str) -> None:
    print(f"\n--- {label} ---")
    time.sleep(PAUSE_SEC)


def ok(msg: str) -> None:
    print(f"  OK: {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL: {msg}")


def main() -> int:
    client = AnyDoClient(session_file="session.json")
    if not client.logged_in:
        print("Session invalid or expired — cannot run live tests without login.")
        return 1

    email = (client.user_info or {}).get("email", "unknown")
    print(f"Authenticated as {email}")

    pause("Incremental sync (default)")
    data = client.get_tasks()
    if not data:
        fail("get_tasks returned None")
        return 1
    tasks = client._get_task_items(data)
    ok(f"sync returned {len(tasks)} tasks")

    pause("Lists")
    lists = client.get_lists(data)
    ok(f"{len(lists)} lists — sample: {lists[0]['name'] if lists else 'none'}")

    pause("Tags")
    tags = client.get_tags(data)
    ok(f"{len(tags)} tags — sample: {tags[0]['name'] if tags else 'none'}")

    pause("Attachments")
    attachments = client.get_attachments(data)
    ok(f"{len(attachments)} attachments")
    if attachments:
        ok(f"sample: {attachments[0]['display_name']} ({attachments[0]['file_size']} bytes)")

    pause("find_tasks (title contains 'test', case-insensitive)")
    matches = client.find_tasks(query="test", tasks_data=data)
    ok(f"{len(matches)} matches")
    if matches:
        ok(f"sample: {matches[0].get('title')}")

    pause("get_overdue_tasks")
    overdue = client.get_overdue_tasks(data)
    ok(f"{len(overdue)} overdue active tasks")

    pause("get_tasks_due_today")
    today = client.get_tasks_due_today(data)
    ok(f"{len(today)} due today")
    if today:
        ok(f"sample: {today[0].get('title')}")

    pause("get_task + get_subtasks on first parent task")
    parents = [t for t in tasks if not t.get("parentGlobalTaskId")]
    if parents:
        sample = parents[0]
        task_id = sample.get("globalTaskId") or sample.get("id")
        fetched = client.get_task(task_id, data)
        ok(f"get_task: {fetched.get('title') if fetched else 'not found'}")
        subtasks = client.get_subtasks(task_id, data)
        ok(f"get_subtasks: {len(subtasks)} children")
    else:
        ok("no parent tasks to sample")

    pause("get_completed_tasks (page 0)")
    completed = client.get_completed_tasks(page=0)
    if completed is None:
        fail("get_completed_tasks returned None")
    else:
        items = completed.get("data", completed if isinstance(completed, list) else [])
        ok(f"completed history: {len(items)} items on page 0")
        if items:
            ok(f"sample: {items[0].get('title')}")

    pause("get_upload_url (presign only, no upload)")
    presign = client.get_upload_url("smoke-test.txt", "text/plain")
    if presign and presign.get("url") and presign.get("fields", {}).get("key"):
        ok(f"presigned POST url: {presign['url'][:60]}...")
    else:
        fail(f"unexpected presign response: {json.dumps(presign)[:200]}")

    pause("Incremental sync with include_archived=True")
    archived_data = client.get_tasks_incremental(include_archived=True, commit=False)
    if archived_data:
        archived_tasks = client._get_task_items(archived_data)
        statuses = {}
        for t in archived_tasks:
            s = t.get("status", "?")
            statuses[s] = statuses.get(s, 0) + 1
        ok(f"incremental+archived: {len(archived_tasks)} tasks, statuses={statuses}")
    else:
        ok("incremental+archived returned None (may be expected if no cursor changes)")

    if attachments:
        pause("download_attachment (read-only, first attachment head only)")
        url = attachments[0]["url"]
        dest = Path("outputs/smoke-test-download.bin")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if client.download_attachment(url, dest):
            ok(f"downloaded {dest.stat().st_size} bytes to {dest}")
            dest.unlink(missing_ok=True)
        else:
            fail("download_attachment failed")

    print("\n=== All read-only smoke tests finished ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
