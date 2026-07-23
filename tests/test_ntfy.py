"""Tests for ntfy notification helpers."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from anydown.client import _is_ntfy_rate_limited, send_ntfy


def test_send_ntfy_rate_limits_repeated_alerts(tmp_path: Path) -> None:
    state_file = tmp_path / ".ntfy-state.json"
    config = {
        "enabled": True,
        "url": "https://ntfy.sh",
        "topic": "test-topic",
        "rate_limit_seconds": 3600,
        "state_file": str(state_file),
        "priority": 3,
    }

    with patch("anydown.client.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)

        assert send_ntfy(config, "Alert", "first", rate_limit_key="watch_failed") is True
        assert send_ntfy(config, "Alert", "second", rate_limit_key="watch_failed") is False
        assert mock_post.call_count == 1


def test_send_ntfy_uses_config_priority(tmp_path: Path) -> None:
    config = {
        "enabled": True,
        "url": "https://ntfy.sh",
        "topic": "test-topic",
        "priority": 2,
        "state_file": str(tmp_path / ".ntfy-state.json"),
    }

    with patch("anydown.client.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        send_ntfy(config, "Alert", "body", rate_limit_key="watch_failed")

    headers = mock_post.call_args.kwargs["headers"]
    assert headers["Priority"] == "2"


def test_rate_limit_state_persists(tmp_path: Path) -> None:
    state_file = tmp_path / ".ntfy-state.json"
    config = {
        "rate_limit_seconds": 3600,
        "state_file": str(state_file),
    }

    state_file.write_text(json.dumps({"watch_failed": time.time()}), encoding="utf-8")
    assert _is_ntfy_rate_limited(config, "watch_failed") is True
