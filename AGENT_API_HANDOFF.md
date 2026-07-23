# Any.do Agent API — Handoff

Lean reference for AI agents consuming Any.do task data from the homelab `anydown` deployment.

## Base URL

| Context | URL |
|---------|-----|
| **Homelab (LAN)** | `http://ubuntu-cloud:8080` or `http://<ubuntu-cloud-ip>:8080` |
| **Local Docker** | `http://localhost:8080` |

`ubuntu-cloud` is VM 102 on Proxmox (DHCP). Resolve IP via UniFi inventory, `ansible-inventory -i inventory/unifi.yaml --host ubuntu-cloud`, or `qm guest cmd 102 network-get-interfaces` on the Proxmox host.

**Caddy reverse proxy:** not wired yet. Reach the API directly on port 8080 from the LAN.

## Authentication

- **Default (homelab):** no auth — API is LAN-only on ubuntu-cloud port 8080.
- **Optional:** set `ANYDOWN_API_TOKEN` on the container; send `Authorization: Bearer <token>` on every request.

## Endpoints

### `GET /health`

Liveness and export availability.

```json
{
  "status": "ok",
  "agent_export_available": true
}
```

### `GET /agent` or `GET /api/agent`

Returns the compact agent export (same shape as `outputs/agent/latest.json`).

**Query params:**

| Param | Values | Effect |
|-------|--------|--------|
| `live` | `1`, `true` | Sync from Any.do first, then return JSON |
| `full` | `1`, `true` | With `live=1`, force full sync |

**Response (200):** agent JSON — pending tasks only, with IDs for mutations.

```json
{
  "exported_at": "2026-07-22 10:12:47",
  "pending_tasks": 476,
  "lists": [{"id": "...", "name": "Personal"}],
  "tags": [{"id": "...", "name": "Buy"}],
  "tasks": [
    {
      "id": "globalTaskId",
      "title": "Buy milk",
      "list_id": "categoryId",
      "list": "Grocery List",
      "tag_ids": ["..."],
      "tags": ["Buy"],
      "due_ms": 1784298291567,
      "note": "optional",
      "subtasks": [{"id": "...", "title": "..."}]
    }
  ]
}
```

**503** if no export exists yet (wait for watch sync or call `POST /sync`).

### `POST /sync` or `POST /api/sync`

Trigger a sync cycle and return the agent JSON. Use sparingly — Any.do full sync is rate-limited (60s cooldown).

Query `?full=1` forces full sync.

## Example usage

```bash
# Health check
curl -s http://ubuntu-cloud:8080/health | jq .

# Read cached export (fast, no Any.do API call)
curl -s http://ubuntu-cloud:8080/agent | jq '.pending_tasks, .tasks[0]'

# Force fresh sync then read
curl -s -X POST http://ubuntu-cloud:8080/sync | jq '.exported_at, .pending_tasks'
```

From Python:

```python
import requests

base = "http://ubuntu-cloud:8080"
data = requests.get(f"{base}/agent", timeout=30).json()
for task in data["tasks"][:5]:
    print(task["id"], task["title"], task.get("list"))
```

## Backup files (SMB)

The container writes exports to a CIFS volume backed by tank:

| Path on tank | Contents |
|--------------|----------|
| `/srv/slow/backup/anydown/agent/latest.json` | **Agent export** (~70–95 KB) — same as `GET /agent` |
| `/srv/slow/backup/anydown/markdown/latest.md` | Human-readable, no task IDs |
| `/srv/slow/backup/anydown/raw-json/latest.json` | Full API payload (~900 KB) |

SMB share: `//tank.local/anydown` (dedicated `anydown` user, not full slow pool).

Agents on the LAN can read `latest.json` from SMB directly if HTTP is unavailable; prefer HTTP for a single integration point.

## Docker deployment (homelab)

| Item | Value |
|------|-------|
| Host | `ubuntu-cloud` (Proxmox VM 102) |
| Image | `ghcr.io/aioue/any.down:1.8.0` |
| Compose | `/opt/anydown/docker-compose.yml` (Ansible `docker_anydown` role) |
| Config | `/etc/anydown/config.json` |
| Session | `/etc/anydown/session/session.json` |
| API port | `8080` (published to host) |
| Watch sync | every 90 ± 10 min (`ANYDOWN_WATCH_INTERVAL` / `ANYDOWN_WATCH_JITTER`) |

Deploy or update:

```bash
ansible-playbook -i inventory/unifi.yaml configure.yml --tags anydown
```

## Mutations (write access)

This HTTP API is **read-only** (export + sync). To create, complete, or delete tasks, use the Python SDK in the `anydown` package:

```python
from anydown import AnyDoClient

client = AnyDoClient(session_file="/path/to/session.json")
# client.complete_task(task_id)  — use IDs from agent export
```

For agents without Python access to session credentials, coordinate with the human — auth requires email + 2FA.

## Agent export semantics

- **Pending tasks only** (`UNCHECKED`). Completed/archived tasks are excluded.
- **Includes task IDs** (`id`, `list_id`, `tag_ids`) needed for SDK mutations.
- **Prefer `GET /agent`** over raw JSON or markdown for token-efficient reads.
- **Incremental sync gotcha:** live `get_tasks()` may return empty when nothing changed; the export always reflects the last successful full/incremental merge.

## Source repo

- Upstream: [aioue/any.down](https://github.com/aioue/any.down)
- Local clone: `external-repos/any.do` (gitignored in proxmox-setup)
- Detailed SDK docs: `AGENT_SDK.md` in that repo
