# Any.down

Backup your [Any.do](https://www.any.do/) tasks to JSON and Markdown.

## 🙏 Acknowledgments
Flow inspired by Any.do's own efficient web implementation, ensuring this client remains respectful of Any.do's infrastructure while providing useful backup capabilities that [do not currently exist on the official site](https://support.any.do/en/articles/8635961-printing-and-exporting-items).

This project is created as a tribute to Any.do's excellent task management service.

## 🌟 Key Features
- **🛡️ Server-Friendly**: Designed to minimize impact on Any.do's infrastructure with smart change detection and incremental sync
- **🔐 Secure Authentication**: Session persistence with email 2FA support
- **📊 Multiple Export Formats**: JSON and Markdown exports


## Quick Start

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Any.do account

### Setup & Run

```bash
git clone <repository-url>
cd anydo-api
uv sync
uv run anydown
```

On first run, the script will prompt you to create a `config.json` with your Any.do credentials. You'll then receive a 2FA code by email to paste in.

Credentials can also be supplied via environment variables (`ANYDO_EMAIL`, `ANYDO_PASSWORD`) or the config file directly.

### CLI Options

```bash
uv run anydown                  # Smart sync (incremental when possible)
uv run anydown --full-sync      # Force full sync
uv run anydown --quiet          # Reduce output
uv run anydown --debug          # Verbose debug logging
```

### Utility Commands

```bash
uv run anydown-debug                # Troubleshoot login issues
uv run anydown-dupes                # Find duplicate tasks (dry run)
uv run anydown-dupes --delete       # Fresh-sync, confirm, then delete via API
uv run anydown-dupes --delete --yes # Skip confirmation prompt
uv run anydown-dupes --keep newest  # Keep newest copy instead of oldest
```

## Docker

Run as a scheduled cron job that syncs every hour:

```bash
docker compose up -d
```

This expects:
- `config.json` in the repo root (mounted read-only)
- `outputs/` directory will be created for exports

The container uses [supercronic](https://github.com/aptible/supercronic) to run hourly syncs. Session state is persisted in a Docker volume. Timezone is autodetected from the host via `/etc/localtime`.

To override the timezone sent to the Any.do API, set `ANYDO_TIMEZONE` in your environment or `docker-compose.yml`.

## Configuration

`config.json` (auto-created on first run, gitignored):

```json
{
  "email": "your@email.com",
  "password": "your_password",
  "save_raw_data": true,
  "auto_export": true,
  "text_wrap_width": 80,
  "dedup_keep": "oldest"
}
```

`dedup_keep` controls which copy `anydown-dupes --delete` preserves: `"oldest"` (default) or `"newest"`. Tasks are only considered duplicates when their title, list, parent task, note, and subtasks all match exactly.

### Manual Session Setup

If you hit login issues (2FA complications, rate limiting), you can extract a session cookie from your browser:

1. Open [Any.do](https://any.do) in your browser, ensure you're logged in
2. Open DevTools (F12) > Application > Cookies > `https://any.do`
3. Copy the `SPRING_SECURITY_REMEMBER_ME_COOKIE` value
4. Create `session.json` (see `session.json.example` for the template)

## Export Output

```
outputs/
├── raw-json/          # Complete API responses
└── markdown/          # Formatted task tables
```

Files are timestamped (`YYYY-MM-DD_HHMM-SS_anydo-tasks.*`) and only created when data has actually changed (SHA-256 hash comparison).

## Development

```bash
uv sync                         # Install all deps (including dev)
uv run pytest -v                # Run tests
uv run pytest -v --cov=anydown  # With coverage
uv run ruff check .             # Lint
uv run ruff check --fix .       # Auto-fix lint
uv run ruff format .            # Format
```

Or via Make:

```bash
make test
make lint
make format
```

### Project Structure

```
anydo-api/
├── src/anydown/
│   ├── __init__.py
│   ├── client.py           # API client library
│   ├── cli.py              # Main CLI entry point
│   ├── debug_login.py      # Login troubleshooting
│   └── find_duplicates.py  # Duplicate finder & remover
├── tests/
├── pyproject.toml          # Config, deps, tool settings
├── uv.lock                 # Locked dependency versions
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
└── crontab
```

## How It Works

- **Smart sync**: Uses incremental sync to check if anything changed since the last run; only performs a full download when changes are detected
- **Session persistence**: Saves auth session to avoid repeated 2FA prompts
- **Change detection**: SHA-256 hashing of exported data prevents writing duplicate files
- **Rate limiting**: Client-side cooldown prevents full syncs more than once per minute
- **Compression**: Requests gzip/br/zstd; decompression handled by the HTTP library
- **Retry logic**: Exponential backoff with automatic retries on 429/5xx

---

*Made with ❤️ for the Any.do community*
