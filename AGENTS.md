# Agent instructions

## Any.do SDK (share with other repos)

Agents in **other local repos** should read **`/Users/tom/src/github/anydo-api/AGENT_SDK.md`** before listing or modifying todos.

Homelab reads: `http://ubuntu-cloud.home.aioue.net:8081/agent?meta=minimal` (see `AGENT_API_HANDOFF.md`).

Copy into another repo's `AGENTS.md`:

```markdown
## Any.do tasks
Read and follow: `/Users/tom/src/github/anydo-api/AGENT_SDK.md`
- SDK: `from anydown import AnyDoClient`
- Session: `/Users/tom/src/github/anydo-api/session.json` (never commit)
- Reads: agent export or homelab GET /agent
- Auth: `cd /Users/tom/src/github/anydo-api && uv run anydown` (human, 2FA)
```

Agents **in this repo** follow `AGENT_SDK.md` too. Prefer `outputs/agent/latest.json` for reads; `src/anydown/client.py` comments explain why clone uses per-task REST instead of full sync.

---

## Learned User Preferences

- Reverse-engineering: prefer network/CDP and IndexedDB over UI clicking
- API capture: avoid full page reloads when possible
- Live validation: read-only smoke tests before mutating
- My Day and Notifications out of scope
- `rotate_client_id` defaults false
- Merged task titles: use `[]` suffix when subtasks/notes; no "(merged cruft)" in titles
- Defer SDK reorder; due+reminder or web UI for "do soon"

## Learned Workspace Facts

- `anydown` — Python 3.13+ (`uv`); backs up tasks to JSON/Markdown; homelab runs as Docker watch + port 8081 API
- `CLONE_SPEC.md` — domain model + API blueprint for planned self-hosted replacement
- **Reads:** agent export (~110 KB) or homelab `/agent`; not incremental sync or raw JSON unless necessary
- **Writes:** create/delete/clone reliable; in-place title/note/due/reminder/reorder unreliable on cookie sessions
- **Rename:** `recreate_task(id, title=…)` — per-task REST fetch (~few KB), new task ID unavoidable
- Incremental sync empty = no changes (normal). Full sync: 60s cooldown, ~900 KB
- Auth: `session.json`; web reorder is IndexedDB + fractional hex `position`

## TODO

- [ ] **Complete sync-engine push** — `_push_sync_tasks()` may need `X-Anydo-Auth` from full login. Capture outbound sync from phone/iOS-on-Mac (mitmproxy/Charles) so title/note/due/reorder work without web UI.
