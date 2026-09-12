# Any.do Agent SDK

Canonical guide for agents listing or modifying Any.do todos via `anydown` / `AnyDoClient`.

**Repo:** `/Users/tom/src/github/homelab/external-repos/any.down` · **Session:** `session.json` (never commit) · **Implementation:** `src/anydown/client.py`

Do not call Any.do HTTP APIs directly unless extending the SDK. Domain detail: `CLONE_SPEC.md`.

---

## Bootstrap

```python
from anydown import AnyDoClient

ANYDO_REPO = "/Users/tom/src/github/homelab/external-repos/any.down"
client = AnyDoClient(session_file=f"{ANYDO_REPO}/session.json")
if not client.logged_in:
    raise RuntimeError(f"Session expired — human must run: cd {ANYDO_REPO} && uv run anydown")
```

Login needs email + 2FA; agents cannot auth alone.

Run from another repo: `uv run --directory /Users/tom/src/github/homelab/external-repos/any.down python script.py`

---

## Data sources (pick by job)

| Source | Size | Use for |
|--------|------|---------|
| **`GET http://ubuntu-cloud.home.aioue.net:8081/agent`** | ~70–110 KB | **Default reads** — pending tasks with `id`, `list_id`, `tag_ids`, notes, subtask summaries |
| Tank SMB `agent/latest.json` | same | Offline backup of homelab export |
| `outputs/agent/latest.json` (local clone) | varies | Dev only — may be a stale placeholder; prefer homelab HTTP |
| **Per-task REST** (`GET /me/tasks/{id}` + attachments) | ~few KB | **`clone_task` / `recreate_task`** when you only have a task ID from agent export |
| **`get_tasks_full()`** / raw-json | ~900 KB | Bulk sync-shaped queries, archived/deleted, whole-account attachment model. **60s cooldown.** |
| **`get_tasks()` incremental** | 0–sparse | Backup/watch only — empty task list when nothing changed is **normal**; do not use for agent ingest |

Agent export: pending (`UNCHECKED`) only. Markdown export has no IDs. Raw JSON only when agent export lacks fields you need.

Homelab HTTP API: see `AGENT_API_HANDOFF.md`. Canonical base: `http://ubuntu-cloud.home.aioue.net:8081`. Reads: `GET /agent`. Writes: `POST /tasks` (create + verify) or Python SDK.

---

## Read patterns

```python
# Offline / token-efficient (preferred)
import json
data = json.loads(open(AnyDoClient.get_latest_export_path("agent")).read())

# Live bulk query (rate-limited)
sync = client.get_tasks_full(include_archived=True)
client.find_tasks(query="milk", list_name="Personal", tasks_data=sync)
client.get_task("globalTaskId", sync)
client.get_completed_tasks(page=0)
```

Sort agent tasks by age: homelab `GET /agent?sort=creation&order=asc&limit=N&meta=minimal`.

---

## Write patterns

### Reliable (create / structure)

```python
client.create_task("Buy milk", category_id=list_id, labels=[tag_id])
client.create_subtask(parent_id, "Subtask")
client.delete_task(task_id, force=True)  # after migrating notes/subtasks
client.recreate_task(task_id, title="New title []")  # rename workaround; ID changes
client.upload_attachment(task_id, "/path/file.png")
```

### Unreliable on cookie sessions (in-place edits)

`update_task`, `set_due_date`, `complete_task`, `set_labels`, reorder — web clients use `POST /api/v14/me/sync`. SDK tries sync push + `PUT /me/tasks`, then **re-fetches** `GET /me/tasks/{id}` if the response echo looks stale. Returns `False` only when refetch also shows the change did not stick (common for title/note/labels on cookie sessions).

**Prefer create-path helpers** for ingest and tagging:

```python
# Prefix title + add labels at create time (delete source)
result = client.recreate_with_labels(
    task_id,
    title="[repo] Buy milk",
    label_ids=[agent_reviewed_tag_id],
)

# Remove all labels (label_ids=[] alone would MERGE — keeps existing tags)
result = client.recreate_with_labels(
    task_id,
    title="[repo] Buy milk",
    label_ids=[],
    replace_labels=True,
)
# or: client.strip_labels(task_id)

# Remove specific labels, then add new ones
result = client.recreate_with_labels(
    task_id,
    title="[repo] Buy milk",
    label_ids=[agent_reviewed_tag_id],
    remove_label_ids=[mistaken_tag_id],
)

# Archive done: CHECKED copy + delete pending source
result = client.complete_via_create(task_id, label_ids=[agent_reviewed_tag_id])

# Post-mutation verify on NEW id (source deleted — verify_task(old_id) returns None)
task = client.verify_task(result["new_id"])
```

`recreate_with_labels` / `complete_via_create` return a result dict (`ok`, `skipped`, `new_id`, `error`). Do **not** chain `recreate_task` + `set_labels` or `complete_task`. After recreate, always verify `result["new_id"]`, not the source id.

**Cambodia ingest:** `ingest_prefix_and_tag(client, task_id, title=..., label_ids=[...])` — keyword-only args; do not pass `title`/`note` positionally.

**Rename when `update_task` fails:**

```python
new = client.recreate_task(task_id, title="Better title []")
new_id = new["globalTaskId"]  # always new — update references
```

`tasks_data=` on recreate is optional; SDK fetches a per-task REST bundle (~few KB) when omitted.

### What recreate preserves

Copies: title, note, due, reminder, tags, priority, repeat rule, all subtasks (incl. completed), attachments (re-linked S3 URLs).

**Cannot preserve:** `globalTaskId`. **Best effort (often ignored):** list position, creation date.

---

## Safety

1. Read before write — confirm task ID/title for destructive ops.
2. No full-sync loops (60s cooldown).
3. My Day and Notifications out of scope.
4. Read-only smoke: `scripts/smoke_test_readonly.py`
5. Hygiene report: `uv run anydown-analyze` (dupes, fuzzy titles, missing `[]`)

### Sync freshness (avoid duplicate creates)

REST creates/deletes (`PUT /me/tasks`, `DELETE /me/tasks/{id}`) do **not** appear in incremental `bg_sync` pulls. The SDK records `last_mutation_timestamp` in session and agent export; when it is **newer than** `last_sync_timestamp`, `get_tasks()` forces a full sync instead of trusting an empty incremental.

**After any SDK mutation**, verify on the **same session** with `client.verify_task(result["new_id"])` (`GET /me/tasks/{id}`; returns `None` for soft-deleted tasks or after recreate deletes the source) — not homelab `GET /agent` cache (watch sync ~90 min; separate session). Homelab export includes `sync_stale: true` when stale; use `POST /sync` (full sync, bypasses 60s cooldown on homelab API) before comparing `pending_tasks` counts.

REST creates/deletes call `invalidate_agent_export()` so on-disk `outputs/agent/latest.json` is marked `sync_stale` until the next watch sync. Cross-machine writes (laptop SDK, homelab `/agent`) still need `POST /sync` on the homelab API or wait for the watch cycle.

`fetch_task()` and `verify_task()` remove soft-deleted entries from a parent's embedded `subTasks`.
Use `create_subtasks()` and `delete_subtasks()` for idempotent, parent-validated batches. For fields
that Any.do refuses to persist through an existing-row update, use `update_task_reliably()`; it
falls back to a create-path recreation and returns the new task ID, because recreation changes
`globalTaskId`.

**Monzo dupe (2026-07-30):** first recreate succeeded; retry script read stale homelab cache (still showed old Monzo), ran `_put_create_task` again → two identical tasks.

---

## Wire into another repo

Add to that repo's `AGENTS.md`:

```markdown
## Any.do tasks
Read and follow: `/Users/tom/src/github/homelab/external-repos/any.down/AGENT_SDK.md`
- SDK: `from anydown import AnyDoClient`
- Session: `/Users/tom/src/github/homelab/external-repos/any.down/session.json`
- Reads: homelab `GET http://ubuntu-cloud.home.aioue.net:8081/agent?meta=minimal` (not local `outputs/agent/latest.json` unless debugging)
- Auth: `cd /Users/tom/src/github/homelab/external-repos/any.down && uv run anydown` (human, 2FA)
```

---

## Reference

| File | Purpose |
|------|---------|
| `AGENT_SDK.md` | This file |
| `AGENT_API_HANDOFF.md` | Homelab HTTP API |
| `CLONE_SPEC.md` | API architecture, mutation paths, clone blueprint |
| `AGENTS.md` | Workspace preferences |
| `src/anydown/client.py` | SDK (comments explain path choices) |
