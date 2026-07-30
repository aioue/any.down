# Any.do Agent API — Handoff

Homelab read API for Any.do task exports. Mutations: Python SDK (`AGENT_SDK.md`).

## Deployment

| Item | Value |
|------|-------|
| Host | **ubuntu-cloud** (Proxmox VM 102) |
| API | `http://ubuntu-cloud.home.aioue.net:8081` (LAN, no Caddy yet) |
| Image | `ghcr.io/aioue/any.down` — watch mode + HTTP sidecar (`ANYDOWN_API_ENABLED=1`) |
| Credentials | `/etc/anydown/` on VM; sourced from `external-repos/any.do` via Ansible |
| Backups | CIFS → tank `/srv/slow/backup/anydown/` (same files as container outputs) |

**Redeploy:** from proxmox-setup: `ansible-playbook -i inventory/unifi.yaml configure.yml --tags anydown`  
**Image-only update:** `playbooks/update-docker.yml --tags anydown`

Upstream source: [aioue/any.down](https://github.com/aioue/any.down). Local dev clone: `external-repos/any.do` (credentials gitignored).

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
| `live=1` | Sync from Any.do first (use sparingly; full sync 60s cooldown with `full=1`) |
| `sort` | `title` · `creation` · `due` |
| `order` | `asc` · `desc` |
| `limit` / `offset` | Pagination after filter/sort |
| `list` / `tag` / `q` | Substring filters |
| `has_due` / `no_due` | Due-date filters |
| `meta=minimal` | Omit list/tag catalogs (token saver) |

Response includes `pending_tasks` (full open count), `matched_tasks`, `returned_tasks`. On-disk export sorts by title; use `?sort=creation` for oldest-first dredging.

**503** if no export yet — wait for watch sync or `POST /sync`.

### `POST /sync` (alias `/api/sync`)

Sync cycle then return agent JSON. `?full=1` forces full sync.

## Examples

```bash
curl -s http://ubuntu-cloud.home.aioue.net:8081/health | jq .
curl -s 'http://ubuntu-cloud.home.aioue.net:8081/agent?sort=creation&order=asc&limit=5&meta=minimal' | jq .
curl -s -X POST http://ubuntu-cloud.home.aioue.net:8081/sync | jq '.exported_at, .pending_tasks'
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

## Mutations

API is **read-only**. Use `AnyDoClient` with session credentials:

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
