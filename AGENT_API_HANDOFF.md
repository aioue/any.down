# Any.do Agent API — Handoff

Homelab HTTP API for Any.do task exports and creates. SDK mutations: `AGENT_SDK.md`.

**Canonical base URL (LAN):** `http://ubuntu-cloud.home.aioue.net:8081`  
Do not use `homelab.local` (does not resolve on the study Mac). Port **8081** is anydown; not 8765.

## Deployment

| Item | Value |
|------|-------|
| Host | **ubuntu-cloud** (Proxmox VM 102) |
| API | `http://ubuntu-cloud.home.aioue.net:8081` (LAN); `https://anydown.home.aioue.net` (Caddy) |
| Image | `ghcr.io/aioue/any.down` — watch mode + HTTP sidecar (`ANYDOWN_API_ENABLED=1`) |
| Credentials | `/etc/anydown/` on VM; sourced from `external-repos/any.down` via Ansible |
| Backups | CIFS → tank `/srv/slow/backup/anydown/` (same files as container outputs) |

**Redeploy:** from proxmox-setup: `ansible-playbook -i inventory/unifi.yaml configure.yml --tags anydown`  
**Image-only update:** `playbooks/update-docker.yml --tags anydown`

Upstream source: [aioue/any.down](https://github.com/aioue/any.down). Local dev clone: `external-repos/any.down` (credentials gitignored).

## Auth

Default: none (LAN-only). Optional: `ANYDOWN_API_TOKEN` + `Authorization: Bearer <token>`.

## Endpoints

### `GET /health`

```json
{"status": "ok", "agent_export_available": true}
```

### `GET /agent` (alias `/api/agent`)

Cached agent export — pending tasks with IDs. Same shape as `outputs/agent/latest.json`.

| Param | Effect |
|-------|--------|
| `live=1` | Full sync from Any.do first, then return export (same as `POST /sync`) |
| `full=1` | Kept for compatibility; API sync always runs full sync |
| `include_completed=1` | Pull CHECKED tasks into `raw-json` on sync (default off; `/agent` response stays pending-only) |
| `sort` | `export` (default) · `title` · `creation` · `due` · `position` |
| `order` | `asc` · `desc` |
| `limit` / `offset` | Pagination after filter/sort |
| `list` / `tag` / `q` | Substring filters |
| `has_due` / `no_due` | Due-date filters |
| `meta=minimal` | Omit list/tag catalogs (token saver) |

Response includes `pending_tasks` (full open count), `matched_tasks`, `returned_tasks`. Default `sort=export` preserves Any.do All Tasks order (hex `position` from sync). Use `?sort=title` for alphabetical or `?sort=creation` for oldest-first dredging.

Agent exports also include `last_sync_timestamp`, `last_mutation_timestamp`, and `sync_stale` (true when REST mutations on the container session post-date the last sync). When `sync_stale` or after mutations from another client, use `?live=1` — cached reads can miss recent creates/deletes.

**503** if no export yet — wait for watch sync or `POST /sync`.

### `POST /sync` (alias `/api/sync`)

Runs a **full** account sync (rate-limit bypassed on the homelab API) then returns agent JSON. Incremental deltas never shrink `pending_tasks`. `?full=1` is optional (same behaviour). `?include_completed=1` includes CHECKED tasks in the raw-json write (not in the agent JSON response).

### Post-write verification (SDK mutations from another machine)

After `recreate_with_labels`, `strip_labels`, or other SDK writes on a laptop session:

```bash
BASE=http://ubuntu-cloud.home.aioue.net:8081
BEFORE=$(curl -s "$BASE/agent" | jq '.pending_tasks')
# ... SDK mutation on laptop ...
curl -s -X POST "$BASE/sync" | jq '.pending_tasks'   # should be within 1 of BEFORE
curl -s "$BASE/agent" | jq '.pending_tasks'          # cached; same count after sync
```

Verify the mutation on the **same SDK session** with `client.verify_task(result["new_id"])` — not `verify_task(old_id)` (source is deleted). `verify_task(old_id)` returns `None` by design after recreate.

### `POST /tasks` (alias `/api/tasks`)

Create a pending task via `AnyDoClient.create_task`, then confirm with `verify_task` (`GET /me/tasks/{id}`). Cached `GET /agent` can lag until the next watch sync; this read is live.

Body:

```json
{"title": "Buy milk", "note": "optional", "category_id": "PERSONAL_LIST_ID"}
```

Optional `labels` (list of tag ids). **200** with `{ok, id, title, status, category_id, confirmed: true}`. **502** if create echoed but verify missed the row.

### `GET /tasks/{id}` (alias `/api/tasks/{id}`)

Same `verify_task` read. Use after create before dismissing a phone notification. **404** if missing.

## Examples

```bash
curl -s http://ubuntu-cloud.home.aioue.net:8081/health | jq .
curl -s 'http://ubuntu-cloud.home.aioue.net:8081/agent?sort=creation&order=asc&limit=5&meta=minimal' | jq .
curl -s -X POST http://ubuntu-cloud.home.aioue.net:8081/sync | jq '.exported_at, .pending_tasks'
curl -s -X POST 'http://ubuntu-cloud.home.aioue.net:8081/sync?full=1&include_completed=1' | jq '.exported_at, .pending_tasks'
curl -s -X POST http://ubuntu-cloud.home.aioue.net:8081/tasks \
  -H 'Content-Type: application/json' \
  -d '{"title":"Buy milk","category_id":"LIST_ID"}'
```

```python
import requests
data = requests.get("http://ubuntu-cloud.home.aioue.net:8081/agent", timeout=30).json()
```

## Backup paths (tank SMB)

| Path | Size | Notes |
|------|------|-------|
| `agent/latest.json` | ~70–110 KB | **Prefer for agents** |
| `markdown/latest.md` | ~45 KB | Human scan, no IDs |
| `raw-json/latest.json` | ~900 KB | Full sync payload; rarely needed |

Prefer HTTP over SMB when both available.

**Authoritative reads:** homelab `GET /agent` or tank SMB `agent/latest.json` after a successful sync. A devcontainer `outputs/agent/latest.json` may be a stale placeholder (`sync_stale: true`, sample `Buy groceries` task) — never treat it as production data.

## Mutations

Prefer `POST /tasks` on this API (create + verify). Other writes still go through `AnyDoClient`:

```python
from anydown import AnyDoClient
client = AnyDoClient(session_file="/path/to/session.json")
client.recreate_task(task_id, title="New title []")  # fetches ~few KB via REST; no full sync
```

Task IDs come from agent export. Auth requires human 2FA.

## Semantics

- Pending tasks only in agent export (`UNCHECKED`).
- Incremental sync may return empty live payloads when unchanged; cached export reflects last successful write.
- ntfy failures: topic `net-aioue-general`, max 1 alert / 24h.
