"""Filter and sort agent exports for token-efficient API responses."""

from __future__ import annotations

from typing import Any

SORT_KEYS = frozenset({"title", "creation", "due"})
ORDER_VALUES = frozenset({"asc", "desc"})
META_MODES = frozenset({"full", "minimal"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def _parse_positive_int(value: str | None, *, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def _sort_key(task: dict[str, Any], sort: str, *, reverse: bool) -> tuple[int, int]:
    """Return (missing_rank, value) so tasks missing sort fields sort last."""
    if sort == "creation":
        value = task.get("creation_ms")
    else:
        value = task.get("due_ms")

    if value is None:
        return (1, 0)
    return (0, -value if reverse else value)


def _matches_list(task: dict[str, Any], needle: str) -> bool:
    haystack = (task.get("list") or "").casefold()
    return needle.casefold() in haystack


def _matches_tag(task: dict[str, Any], needle: str) -> bool:
    tags = task.get("tags") or []
    needle_cf = needle.casefold()
    return any(needle_cf in tag.casefold() for tag in tags)


def _matches_q(task: dict[str, Any], needle: str) -> bool:
    title = (task.get("title") or "").casefold()
    note = (task.get("note") or "").casefold()
    needle_cf = needle.casefold()
    return needle_cf in title or needle_cf in note


def filter_agent_export(export: dict[str, Any], query: dict[str, list[str]]) -> dict[str, Any]:
    """Apply query-string filters to an agent export without mutating the input."""
    sort = (query.get("sort", ["title"])[0] or "title").strip().lower()
    if sort not in SORT_KEYS:
        sort = "title"

    order = (query.get("order", ["asc"])[0] or "asc").strip().lower()
    if order not in ORDER_VALUES:
        order = "asc"

    meta = (query.get("meta", ["full"])[0] or "full").strip().lower()
    if meta not in META_MODES:
        meta = "full"

    limit = _parse_positive_int(query.get("limit", [None])[0])
    offset = _parse_positive_int(query.get("offset", [None])[0], default=0) or 0

    list_filter = (query.get("list", [""])[0] or "").strip()
    tag_filter = (query.get("tag", [""])[0] or "").strip()
    q_filter = (query.get("q", [""])[0] or "").strip()
    has_due = _truthy(query.get("has_due", [None])[0])
    no_due = _truthy(query.get("no_due", [None])[0])

    tasks = list(export.get("tasks") or [])
    pending_tasks = int(export.get("pending_tasks") or len(tasks))

    if list_filter:
        tasks = [task for task in tasks if _matches_list(task, list_filter)]
    if tag_filter:
        tasks = [task for task in tasks if _matches_tag(task, tag_filter)]
    if q_filter:
        tasks = [task for task in tasks if _matches_q(task, q_filter)]
    if has_due:
        tasks = [task for task in tasks if task.get("due_ms") is not None]
    if no_due:
        tasks = [task for task in tasks if task.get("due_ms") is None]

    reverse = order == "desc"
    if sort == "title":
        tasks.sort(key=lambda task: (task.get("title") or "").strip().lower(), reverse=reverse)
    else:
        tasks.sort(key=lambda task: _sort_key(task, sort, reverse=reverse))

    matched_tasks = len(tasks)
    if offset:
        tasks = tasks[offset:]
    if limit is not None:
        tasks = tasks[:limit]

    payload: dict[str, Any] = {
        "exported_at": export.get("exported_at"),
        "pending_tasks": pending_tasks,
        "matched_tasks": matched_tasks,
        "returned_tasks": len(tasks),
        "tasks": tasks,
    }

    if meta == "full":
        if "lists" in export:
            payload["lists"] = export["lists"]
        if "tags" in export:
            payload["tags"] = export["tags"]

    if offset:
        payload["offset"] = offset
    if limit is not None:
        payload["limit"] = limit
    if sort != "title" or order != "asc":
        payload["sort"] = sort
        payload["order"] = order

    filters_applied: list[str] = []
    if list_filter:
        filters_applied.append("list")
    if tag_filter:
        filters_applied.append("tag")
    if q_filter:
        filters_applied.append("q")
    if has_due:
        filters_applied.append("has_due")
    if no_due:
        filters_applied.append("no_due")
    if filters_applied:
        payload["filters"] = filters_applied

    return payload
