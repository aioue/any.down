"""Lightweight HTTP API for agent-formatted Any.do exports."""

from __future__ import annotations

import json
import logging
import os
import threading
from argparse import Namespace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from anydown.agent_query import filter_agent_export
from anydown.cli import get_credentials_from_env, load_config, run_sync
from anydown.client import AnyDoClient

logger = logging.getLogger(__name__)

AGENT_EXPORT_PATH = os.path.join("outputs", "agent", "latest.json")
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080

_sync_lock = threading.Lock()


def read_agent_export() -> dict[str, Any] | None:
    """Load the latest agent export JSON from disk."""
    path = AnyDoClient.get_latest_export_path("agent")
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def agent_export_available() -> bool:
    return read_agent_export() is not None


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _authorized(headers: Any) -> bool:
    token = os.environ.get("ANYDOWN_API_TOKEN", "").strip()
    if not token:
        return True
    auth = headers.get("Authorization", "")
    return auth == f"Bearer {token}"


def _bootstrap_client() -> tuple[AnyDoClient | None, str | None]:
    """Create an authenticated client for API-triggered syncs."""
    config = load_config() or {}
    env_credentials = get_credentials_from_env()
    if env_credentials:
        email, password, *_rest = env_credentials
    else:
        email = config.get("email")
        password = config.get("password")
    if not email or not password:
        return None, "Missing credentials in config.json or ANYDO_EMAIL/ANYDO_PASSWORD"

    session_file = os.environ.get("ANYDO_SESSION_FILE", "session.json")
    client = AnyDoClient(
        session_file=session_file,
        text_wrap_width=config.get("text_wrap_width", 80),
        rotate_client_id=config.get("rotate_client_id", False),
    )
    if client.logged_in:
        return client, None
    if not client.login(email, password):
        return None, "Authentication failed — session expired or credentials invalid"
    return client, None


def _query_flag(query: dict[str, list[str]], key: str) -> bool:
    return query.get(key, ["0"])[0].lower() in ("1", "true", "yes")


def _task_id_from_path(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) == 2 and parts[0] == "tasks":
        return unquote(parts[1])
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "tasks":
        return unquote(parts[2])
    return None


def _task_payload(task: dict[str, Any]) -> dict[str, Any]:
    task_id = task.get("globalTaskId") or task.get("id") or ""
    return {
        "ok": True,
        "id": task_id,
        "title": task.get("title") or "",
        "status": task.get("status") or "",
        "category_id": task.get("categoryId") or "",
        "confirmed": True,
    }


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return {}
    if length > 65536:
        return None
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def create_and_verify_task(
    title: str,
    *,
    category_id: str | None = None,
    note: str = "",
    labels: list[str] | None = None,
) -> tuple[dict[str, Any] | None, int, str | None]:
    """Create a task then confirm it via GET /me/tasks/{id}. Returns (payload, status, error)."""
    client, error = _bootstrap_client()
    if error or client is None:
        return None, HTTPStatus.SERVICE_UNAVAILABLE, error
    created = client.create_task(title, category_id=category_id, note=note, labels=labels)
    if not created:
        return None, HTTPStatus.BAD_GATEWAY, "Any.do create_task failed"
    task_id = created.get("globalTaskId") or created.get("id") or ""
    verified = client.verify_task(task_id) if task_id else None
    if not verified:
        return (
            {"ok": False, "id": task_id, "error": "create returned but verify_task missed the row"},
            HTTPStatus.BAD_GATEWAY,
            None,
        )
    return _task_payload(verified), HTTPStatus.OK, None


def read_verified_task(task_id: str) -> tuple[dict[str, Any] | None, int, str | None]:
    """Confirm a task exists via GET /me/tasks/{id}. Returns (payload, status, error)."""
    client, error = _bootstrap_client()
    if error or client is None:
        return None, HTTPStatus.SERVICE_UNAVAILABLE, error
    verified = client.verify_task(task_id)
    if not verified:
        return {"ok": False, "id": task_id, "error": "not found"}, HTTPStatus.NOT_FOUND, None
    return _task_payload(verified), HTTPStatus.OK, None


def sync_and_read_agent(
    *, full_sync: bool = False, include_completed: bool = False
) -> tuple[dict[str, Any] | None, str | None]:
    """Run one sync cycle and return the agent export payload."""
    with _sync_lock:
        client, error = _bootstrap_client()
        if error or client is None:
            return None, error

        config = load_config() or {}
        env_credentials = get_credentials_from_env()
        if env_credentials:
            _email, _password, save_raw, auto_export, *_rest = env_credentials
        else:
            save_raw = config.get("save_raw_data", True)
            auto_export = config.get("auto_export", True)

        args = Namespace(
            full_sync=full_sync or include_completed,
            incremental_only=False,
            include_completed=include_completed,
        )
        if not run_sync(client, args, save_raw, auto_export):
            return None, "Sync failed"

        export = read_agent_export()
        if export is None:
            return None, "Sync completed but no agent export is available yet"
        return export, None


class AnydownAPIHandler(BaseHTTPRequestHandler):
    """Minimal JSON API for agent exports."""

    server_version = "anydown-api/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    def _reject_unauthorized(self) -> None:
        _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})

    def _respond_agent(self, export: dict[str, Any], query: dict[str, list[str]]) -> None:
        payload = filter_agent_export(export, query)
        _json_response(self, HTTPStatus.OK, payload)

    def _route_get(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        query = parse_qs(urlparse(self.path).query)

        if path == "/health":
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "agent_export_available": agent_export_available(),
                },
            )
            return

        if path in ("/agent", "/api/agent"):
            if _query_flag(query, "live"):
                export, error = sync_and_read_agent(
                    full_sync=_query_flag(query, "full"),
                    include_completed=_query_flag(query, "include_completed"),
                )
                if error:
                    _json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {"error": error})
                    return
                self._respond_agent(export or {}, query)
                return

            export = read_agent_export()
            if export is None:
                _json_response(
                    self,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "No agent export yet — wait for watch sync or POST /sync"},
                )
                return
            if AnyDoClient.export_sync_stale(export):
                logger.warning(
                    "Serving cached agent export with sync_stale=true — "
                    "REST mutations may be missing; use ?live=1"
                )
            self._respond_agent(export, query)
            return

        task_id = _task_id_from_path(path)
        if task_id:
            payload, status, error = read_verified_task(task_id)
            if error:
                _json_response(self, status, {"error": error})
                return
            _json_response(self, status, payload or {"error": "not found"})
            return

        _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_GET(self) -> None:
        if not _authorized(self.headers):
            self._reject_unauthorized()
            return
        self._route_get()

    def do_POST(self) -> None:
        if not _authorized(self.headers):
            self._reject_unauthorized()
            return

        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/tasks", "/api/tasks"):
            self._create_task()
            return
        if path not in ("/sync", "/api/sync"):
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return

        query = parse_qs(urlparse(self.path).query)
        export, error = sync_and_read_agent(
            full_sync=_query_flag(query, "full"),
            include_completed=_query_flag(query, "include_completed"),
        )
        if error:
            _json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {"error": error})
            return
        self._respond_agent(export or {}, query)

    def _create_task(self) -> None:
        data = _read_json_body(self)
        if data is None:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
            return
        title = str(data.get("title") or "").strip()
        if not title:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "title is required"})
            return
        category_id = str(data.get("category_id") or data.get("categoryId") or "").strip() or None
        note = str(data.get("note") or "")
        labels = data.get("labels")
        if labels is not None and not isinstance(labels, list):
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "labels must be a list"})
            return
        label_ids = [str(item) for item in labels] if isinstance(labels, list) else None
        payload, status, error = create_and_verify_task(
            title,
            category_id=category_id,
            note=note,
            labels=label_ids,
        )
        if error:
            _json_response(self, status, {"error": error})
            return
        _json_response(self, status, payload or {"error": "create failed"})

    def do_HEAD(self) -> None:
        if not _authorized(self.headers):
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.end_headers()
            return
        if urlparse(self.path).path.rstrip("/") in ("/health", "/agent", "/api/agent"):
            self.send_response(HTTPStatus.OK)
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()


def serve(host: str | None = None, port: int | None = None) -> None:
    """Run the API server until interrupted."""
    bind_host = host or os.environ.get("ANYDOWN_API_HOST", DEFAULT_HOST)
    bind_port = int(port or os.environ.get("ANYDOWN_API_PORT", DEFAULT_PORT))

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = ThreadingHTTPServer((bind_host, bind_port), AnydownAPIHandler)
    logger.info("anydown API listening on http://%s:%d", bind_host, bind_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("anydown API shutting down")
    finally:
        server.server_close()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
