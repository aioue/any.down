#!/usr/bin/env python3
"""
Deterministic task hygiene checks for Any.do agent exports.

Reads a pending-task export (local ``outputs/agent/latest.json``, a path, or
``--url`` for the homelab agent API) and reports duplicates, fuzzy overlaps,
missing ``[]`` checklist suffixes, and weak titles. No sync required when a
cached export is fresh enough.

Usage:
    anydown-analyze                    # local agent export or homelab API
    anydown-analyze --json             # machine-readable report
    anydown-analyze path/to/export.json
    anydown-analyze --url http://ubuntu-cloud.home.aioue.net:8081/agent
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import urllib.request
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from anydown.client import AnyDoClient
from anydown.find_duplicates import find_duplicate_groups, load_tasks_from_backup

logger = logging.getLogger(__name__)

DEFAULT_AGENT_URL = "http://ubuntu-cloud.home.aioue.net:8081/agent"
MIN_SUBTASKS_FOR_BRACKET = 3
FUZZY_RATIO_THRESHOLD = 0.78
PREFIX_LEN = 35
MIN_TITLE_LEN_FUZZY = 10
MIN_TITLE_LEN_PREFIX = 12
MIN_TITLE_LEN_SUBSTRING = 10

WEAK_TITLE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^$", "empty title"),
    (r"^.{1,2}$", "very short title"),
    (r"^(todo|task|fix|check|review)$", "generic placeholder"),
    (r"\(\s*merged\s|cruft\s*\)", "merge cruft in title"),
    (r"\b(TBD|FIXME|XXX|WIP)\b", "placeholder marker"),
)


def _normalise_title(title: str) -> str:
    """Lowercase title without trailing ``[]`` and extra punctuation."""
    base = re.sub(r"\s*\[\]\s*$", "", title.strip())
    base = re.sub(r"[^\w\s]", " ", base.lower())
    return re.sub(r"\s+", " ", base).strip()


def _title_prefix(title: str, length: int = PREFIX_LEN) -> str:
    return _normalise_title(title)[:length]


def _weak_title_reasons(title: str) -> list[str]:
    reasons: list[str] = []
    stripped = title.strip()
    for pattern, reason in WEAK_TITLE_PATTERNS:
        if re.search(pattern, stripped, flags=re.IGNORECASE):
            reasons.append(reason)
    if len(stripped) > 80 and not stripped.endswith("[]") and stripped.count(" ") > 12:
        reasons.append("long unstructured title")
    return reasons


def load_agent_export(source: str | None = None, *, url: str | None = None) -> dict[str, Any]:
    """Load an agent-shaped export dict."""
    if url:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.loads(response.read())

    if source:
        return json.loads(Path(source).read_text(encoding="utf-8"))

    latest = Path("outputs/agent/latest.json")
    if latest.exists():
        return json.loads(latest.read_text(encoding="utf-8"))

    logger.info("No local export; fetching %s", DEFAULT_AGENT_URL)
    with urllib.request.urlopen(DEFAULT_AGENT_URL, timeout=30) as response:
        return json.loads(response.read())


def find_normalized_title_collisions(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        key = _normalise_title(task.get("title", ""))
        if len(key) >= 6:
            groups[key].append(task)
    return [
        {"normalized_title": key, "tasks": group}
        for key, group in sorted(groups.items(), key=lambda item: -len(item[1]))
        if len(group) > 1
    ]


def find_fuzzy_title_pairs(
    tasks: list[dict[str, Any]], *, threshold: float = FUZZY_RATIO_THRESHOLD
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(tasks):
        left_key = _normalise_title(left.get("title", ""))
        if len(left_key) < MIN_TITLE_LEN_FUZZY:
            continue
        for right in tasks[index + 1 :]:
            right_key = _normalise_title(right.get("title", ""))
            if len(right_key) < MIN_TITLE_LEN_FUZZY:
                continue
            ratio = SequenceMatcher(None, left_key, right_key).ratio()
            if threshold <= ratio < 1.0:
                pairs.append(
                    {
                        "ratio": round(ratio, 3),
                        "left": _task_summary(left),
                        "right": _task_summary(right),
                    }
                )
    pairs.sort(key=lambda item: item["ratio"], reverse=True)
    return pairs


def find_prefix_collisions(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        prefix = _title_prefix(task.get("title", ""))
        if len(prefix) >= MIN_TITLE_LEN_PREFIX:
            groups[prefix].append(task)
    return [
        {"prefix": prefix, "tasks": [_task_summary(task) for task in group]}
        for prefix, group in sorted(groups.items(), key=lambda item: -len(item[1]))
        if len(group) > 1
    ]


def find_substring_title_pairs(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(tasks):
        left_title = left.get("title", "").strip().lower()
        if len(left_title) < MIN_TITLE_LEN_SUBSTRING:
            continue
        for right in tasks[index + 1 :]:
            right_title = right.get("title", "").strip().lower()
            if len(right_title) < MIN_TITLE_LEN_SUBSTRING:
                continue
            if left_title != right_title and (left_title in right_title or right_title in left_title):
                pairs.append({"left": _task_summary(left), "right": _task_summary(right)})
    return pairs


def find_missing_bracket_suffix(
    tasks: list[dict[str, Any]], *, min_subtasks: int = MIN_SUBTASKS_FOR_BRACKET
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for task in tasks:
        subtasks = task.get("subtasks") or []
        title = (task.get("title") or "").strip()
        if len(subtasks) >= min_subtasks and not title.endswith("[]"):
            findings.append(
                {
                    **_task_summary(task),
                    "subtask_count": len(subtasks),
                    "reason": f"{len(subtasks)}+ subtasks without [] suffix",
                }
            )
    findings.sort(key=lambda item: item["subtask_count"], reverse=True)
    return findings


def find_weak_titles(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for task in tasks:
        reasons = _weak_title_reasons(task.get("title", ""))
        note = (task.get("note") or "").strip()
        subtasks = task.get("subtasks") or []
        if (
            len(subtasks) >= MIN_SUBTASKS_FOR_BRACKET
            and not (task.get("title") or "").rstrip().endswith("[]")
            and "missing [] suffix on checklist parent" not in reasons
        ):
            reasons.append("missing [] suffix on checklist parent")
        if len(note) > 80 and len(note) > 2 * len(task.get("title") or ""):
            reasons.append("note much longer than title")
        if reasons:
            findings.append({**_task_summary(task), "reasons": reasons})
    return findings


def find_strict_duplicates_from_raw(raw_path: str | None = None) -> list[dict[str, Any]]:
    """Strict duplicate groups using find_duplicates identity (requires raw sync JSON)."""
    try:
        if raw_path:
            data = json.loads(Path(raw_path).read_text(encoding="utf-8"))
            tasks = data.get("models", {}).get("task", {}).get("items", [])
        else:
            tasks = load_tasks_from_backup()
    except FileNotFoundError:
        return []

    pending_parent_ids = {
        task.get("globalTaskId") or task.get("id")
        for task in tasks
        if task.get("status") == "UNCHECKED" and not task.get("parentGlobalTaskId")
    }
    groups = find_duplicate_groups(tasks)
    results: list[dict[str, Any]] = []
    for _key, members in groups.items():
        parents = [
            task
            for task in members
            if (task.get("globalTaskId") or task.get("id")) in pending_parent_ids
        ]
        if len(parents) > 1:
            results.append(
                {
                    "title": members[0].get("title", ""),
                    "tasks": [
                        {
                            "id": task.get("globalTaskId") or task.get("id"),
                            "list_id": task.get("categoryId"),
                            "due_date": task.get("dueDate"),
                        }
                        for task in parents
                    ],
                }
            )
    return results


def analyze_export(export: dict[str, Any], *, raw_json_path: str | None = None) -> dict[str, Any]:
    """Run all deterministic checks and return a structured report."""
    tasks = export.get("tasks") or []
    sync_stale = AnyDoClient.export_sync_stale(export)
    return {
        "exported_at": export.get("exported_at"),
        "last_sync_timestamp": export.get("last_sync_timestamp"),
        "last_mutation_timestamp": export.get("last_mutation_timestamp"),
        "sync_stale": sync_stale,
        "pending_tasks": export.get("pending_tasks", len(tasks)),
        "strict_duplicates": find_strict_duplicates_from_raw(raw_json_path),
        "normalized_title_collisions": find_normalized_title_collisions(tasks),
        "fuzzy_title_pairs": find_fuzzy_title_pairs(tasks),
        "prefix_collisions": find_prefix_collisions(tasks),
        "substring_title_pairs": find_substring_title_pairs(tasks),
        "missing_bracket_suffix": find_missing_bracket_suffix(tasks),
        "weak_titles": find_weak_titles(tasks),
    }


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get("id"),
        "title": task.get("title", ""),
        "list": task.get("list"),
        "due_ms": task.get("due_ms"),
        "subtask_count": len(task.get("subtasks") or []),
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"Export: {report.get('exported_at')} — {report.get('pending_tasks')} pending tasks")
    if report.get("sync_stale"):
        print(
            "WARNING: sync_stale=true — export predates REST mutations; "
            "run sync or use GET /agent?live=1 before trusting dupes/missing tasks\n"
        )
    else:
        print()

    sections = [
        ("STRICT DUPLICATES (raw sync identity)", report["strict_duplicates"], _print_strict_group),
        ("NORMALIZED TITLE COLLISIONS", report["normalized_title_collisions"], _print_norm_group),
        ("FUZZY TITLE PAIRS", report["fuzzy_title_pairs"], _print_fuzzy_pair),
        ("PREFIX COLLISIONS", report["prefix_collisions"], _print_prefix_group),
        ("SUBSTRING TITLE PAIRS", report["substring_title_pairs"], _print_substring_pair),
        ("MISSING [] ON CHECKLIST PARENTS", report["missing_bracket_suffix"], _print_finding),
        ("WEAK TITLES / NOTES", report["weak_titles"], _print_weak),
    ]

    for heading, items, printer in sections:
        print(f"=== {heading} ({len(items)}) ===")
        if not items:
            print("  (none)\n")
            continue
        for item in items:
            printer(item)
        print()


def _print_strict_group(item: dict[str, Any]) -> None:
    print(f"  [{len(item['tasks'])}x] {item['title']!r}")
    for task in item["tasks"]:
        print(f"    id={task['id']} due={task['due_date']}")


def _print_norm_group(item: dict[str, Any]) -> None:
    print(f"  [{len(item['tasks'])}x] norm={item['normalized_title']!r}")
    for task in item["tasks"]:
        print(f"    [{task.get('list')}] {task['title'][:70]!r} id={task['id']}")


def _print_fuzzy_pair(item: dict[str, Any]) -> None:
    print(f"  {item['ratio']:.2f} | {item['left']['title'][:55]}")
    print(f"         | {item['right']['title'][:55]}")
    print(f"         ids: {item['left']['id']} / {item['right']['id']}")


def _print_prefix_group(item: dict[str, Any]) -> None:
    print(f"  [{len(item['tasks'])}x] prefix={item['prefix']!r}")
    for task in item["tasks"]:
        print(f"    {task['title'][:70]} id={task['id']}")


def _print_substring_pair(item: dict[str, Any]) -> None:
    print(f"  {item['left']['title'][:55]}")
    print(f"    vs {item['right']['title'][:55]}")
    print(f"    ids: {item['left']['id']} / {item['right']['id']}")


def _print_finding(item: dict[str, Any]) -> None:
    print(f"  [{item['subtask_count']}] {item['title'][:70]} id={item['id']}")


def _print_weak(item: dict[str, Any]) -> None:
    print(f"  {item['title'][:70]} id={item['id']}")
    print(f"    reasons: {', '.join(item['reasons'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic Any.do task hygiene analysis")
    parser.add_argument("source", nargs="?", help="Agent export JSON path (default: outputs/agent/latest.json)")
    parser.add_argument("--url", help=f"Fetch agent export from URL (default homelab: {DEFAULT_AGENT_URL})")
    parser.add_argument("--raw-json", help="Optional raw sync JSON for strict duplicate detection")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only print when findings exist")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s")

    export = load_agent_export(args.source, url=args.url)
    raw_path = args.raw_json or ("outputs/raw-json/latest.json" if Path("outputs/raw-json/latest.json").exists() else None)
    report = analyze_export(export, raw_json_path=raw_path)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    finding_count = sum(
        len(report[key])
        for key in report
        if isinstance(report[key], list) and key not in ("exported_at",)
    )
    if args.quiet and finding_count == 0:
        return

    print_report(report)


if __name__ == "__main__":
    main()
