# Any.do Agent API — Handoff

Lean reference for AI agents consuming Any.do task data from the homelab `anydown` deployment.

## Dockerized anydown

`anydown` is the homelab deployment of [any.down](https://github.com/aioue/any.down): a Python CLI that exports Any.do tasks to JSON/Markdown and backs them up to SMB storage.

In production it runs as a **Docker container** on the `ubuntu-cloud` Proxmox VM:

| Item | Value |
|------|-------|
| Image | `ghcr.io/aioue/any.down:1.8.0` (built by GitHub Actions on push/release to `aioue/any.down`) |
| Mode | `anydown --watch` — periodic sync (default 90 ± 10 min) |
| HTTP API | Sidecar on port **8081** inside the container (`ANYDOWN_API_ENABLED=1`) |
| Compose | `/opt/anydown/docker-compose.yml` (Ansible-managed) |
| Outputs | CIFS volume → `//tank.local/anydown` → `/srv/slow/backup/anydown` on tank |

The image entrypoint starts watch mode and the HTTP API together. Agents should prefer the HTTP API over reading SMB files directly.

## Where it runs

| Item | Value |
|------|-------|
| Host | **ubuntu-cloud** — Proxmox VM 102 (Ubuntu, DHCP via UniFi) |
| DNS | `ubuntu-cloud.home.aioue.net` (Route53 A record, LAN IP) |
| API base URL | `http://ubuntu-cloud.home.aioue.net:8081` |
| Config | `/etc/anydown/config.json` |
| Session | `/etc/anydown/session/session.json` |

**Caddy reverse proxy:** not wired yet. Reach the API directly on port 8081 from the LAN using the DNS name above (not a raw IP).

## Redeploy path

Infrastructure is managed from the **proxmox-setup** repo (`/Users/tom/src/github/proxmox-setup`):

```bash
ansible-playbook -i inventory/unifi.yaml configure.yml --tags anydown
```

This role (`docker_anydown`) pulls the pinned image, syncs credentials, renders compose, and restarts the container. Use `--tags caddy,dns` (or include `dns` without skip) when the Route53 A record for `ubuntu-cloud.home.aioue.net` needs updating after an IP change.

Docker image updates only (no credential/compose changes):

```bash
ansible-playbook -i inventory/unifi.yaml playbooks/update-docker.yml --tags anydown
```

## Two code copies

| Location | Purpose |
|----------|---------|
| `external-repos/any.do` | Local gitignored clone inside proxmox-setup. Holds dev docs (`AGENT_SDK.md`, this file), and **credentials** (`config.json`, `session.json`) that must never be committed. |
| [github.com/aioue/any.down](https://github.com/aioue/any.down) | Upstream source repo. GitHub Actions builds and publishes `ghcr.io/aioue/any.down` tags. Feature work and releases happen here; proxmox-setup consumes the image. |

Refresh the local clone: `./update-external-repos.sh` or `configure.yml --tags external-repos`.

## Deploy and secrets in proxmox-setup

| File / role | What it does |
|-------------|--------------|
| `roles/docker_anydown/` | Compose template, CIFS SMB volume, credential sync, ntfy merge into `config.json`, container lifecycle |
| `inventory/group_vars/all/maintenance.yml` | `anydown_credentials_src`, image pin (`ghcr.io/aioue/any.down:1.8.0`), `anydown_api_port`, `ubuntu_cloud_fqdn`, `anydown_api_base_url`, shared `homelab_ntfy_topic` |
| `roles/lxc_tank/vars/secrets.yml` (vault) | `samba_anydown_password` for the dedicated SMB user on tank |
| `inventory/group_vars/all/secrets.yml` (vault) | Route53 AWS keys for Caddy DNS (if editing DNS records) |

**Vault editing:** `pilfer open` before editing encrypted files, `pilfer close` after.

**No secrets in any.down repo** — session tokens and SMB passwords live only in proxmox-setup vault and on the ubuntu-cloud host at `/etc/anydown/`.

Credentials are copied from `anydown_credentials_src` (default `external-repos/any.do`) on each `--tags anydown` deploy.

## ntfy alerts

Shared homelab notification topic (also used by Dockhand and homelab-maintenance):

| Setting | Value |
|---------|-------|
| Topic | `net-aioue-general` (`homelab_ntfy_topic` in `maintenance.yml`) |
| URL | `https://ntfy.sh` |
| Priority | 3 (normal) |
| Failure rate limit | Max **1 alert per 24 hours** (`anydown_ntfy_rate_limit_seconds: 86400`) |
| Watch-start notifications | **Off** (`anydown_ntfy_notify_on_watch_start: false`) |
| Dockhand container notify | **Disabled** (`dockhand.notify=false` label on anydown container) |

Ansible merges these settings into `config.json` on deploy so the container and Dockhand stay aligned.

---

## Base URL

| Context | URL |
|---------|-----|
| **Homelab (LAN)** | `http://ubuntu-cloud.home.aioue.net:8081` |
| **Local Docker** | `http://localhost:8080` |

## Authentication

- **Default (homelab):** no auth — API is LAN-only on port 8081.
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
curl -s http://ubuntu-cloud.home.aioue.net:8081/health | jq .

# Read cached export (fast, no Any.do API call)
curl -s http://ubuntu-cloud.home.aioue.net:8081/agent | jq '.pending_tasks, .tasks[0]'

# Force fresh sync then read
curl -s -X POST http://ubuntu-cloud.home.aioue.net:8081/sync | jq '.exported_at, .pending_tasks'
```

From Python:

```python
import requests

base = "http://ubuntu-cloud.home.aioue.net:8081"
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
