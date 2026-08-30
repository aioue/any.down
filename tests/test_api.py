"""Tests for the anydown HTTP API."""

import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

import requests

from anydown.api import AnydownAPIHandler, agent_export_available, read_agent_export, sync_and_read_agent


class TestAgentExportHelpers(unittest.TestCase):
    def test_read_agent_export_missing(self):
        with patch("anydown.api.AnyDoClient.get_latest_export_path", return_value=None):
            self.assertIsNone(read_agent_export())

    def test_agent_export_available_false_when_missing(self):
        with patch("anydown.api.read_agent_export", return_value=None):
            self.assertFalse(agent_export_available())


class TestAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), AnydownAPIHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_health(self):
        response = requests.get(f"{self.base_url}/health", timeout=5)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertIn("agent_export_available", payload)

    def test_agent_not_found_without_export(self):
        with patch("anydown.api.read_agent_export", return_value=None):
            response = requests.get(f"{self.base_url}/agent", timeout=5)
        self.assertEqual(response.status_code, 503)
        self.assertIn("error", response.json())

    def test_agent_returns_export(self):
        sample = {"exported_at": "2026-01-01 00:00:00", "tasks": [], "lists": [], "tags": []}
        with patch("anydown.api.read_agent_export", return_value=sample):
            response = requests.get(f"{self.base_url}/api/agent", timeout=5)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["exported_at"], "2026-01-01 00:00:00")
        self.assertEqual(payload["returned_tasks"], 0)

    def test_agent_sort_and_limit(self):
        sample = {
            "exported_at": "2026-01-01 00:00:00",
            "pending_tasks": 2,
            "tasks": [
                {"id": "b", "title": "Beta", "creation_ms": 2000},
                {"id": "a", "title": "Alpha", "creation_ms": 1000},
            ],
            "lists": [],
            "tags": [],
        }
        with patch("anydown.api.read_agent_export", return_value=sample):
            response = requests.get(
                f"{self.base_url}/agent?sort=creation&order=asc&limit=1&meta=minimal",
                timeout=5,
            )
        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["tasks"][0]["id"], "a")
        self.assertEqual(payload["returned_tasks"], 1)
        self.assertNotIn("lists", payload)

    def test_sync_endpoint(self):
        sample = {"exported_at": "2026-01-01 00:00:00", "tasks": [], "lists": [], "tags": []}
        with patch("anydown.api.sync_and_read_agent", return_value=(sample, None)):
            response = requests.post(f"{self.base_url}/sync", timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tasks"], [])

    def test_sync_passes_include_completed(self):
        sample = {"exported_at": "2026-01-01 00:00:00", "tasks": [], "lists": [], "tags": []}
        with patch("anydown.api.sync_and_read_agent", return_value=(sample, None)) as mock_sync:
            response = requests.post(
                f"{self.base_url}/sync?full=1&include_completed=1",
                timeout=5,
            )
        self.assertEqual(response.status_code, 200)
        mock_sync.assert_called_once_with(full_sync=True, include_completed=True)

    def test_sync_forces_full_sync_when_include_completed_only(self):
        sample = {"exported_at": "2026-01-01 00:00:00", "tasks": [], "lists": [], "tags": []}
        with patch("anydown.api.run_sync", return_value=True) as mock_run_sync:
            with patch("anydown.api.read_agent_export", return_value=sample):
                with patch("anydown.api._bootstrap_client") as mock_boot:
                    mock_client = Mock()
                    mock_boot.return_value = (mock_client, None)
                    with patch("anydown.api.load_config", return_value={"save_raw_data": True, "auto_export": True}):
                        export, error = sync_and_read_agent(full_sync=False, include_completed=True)
        self.assertIsNone(error)
        self.assertEqual(export, sample)
        sync_args = mock_run_sync.call_args[0][1]
        self.assertTrue(sync_args.full_sync)
        self.assertTrue(sync_args.include_completed)

    def test_auth_required_when_token_set(self):
        sample = {"exported_at": "2026-01-01 00:00:00", "tasks": []}
        with patch.dict(os.environ, {"ANYDOWN_API_TOKEN": "secret"}):
            with patch("anydown.api.read_agent_export", return_value=sample):
                unauthorized = requests.get(f"{self.base_url}/agent", timeout=5)
                authorized = requests.get(
                    f"{self.base_url}/agent",
                    headers={"Authorization": "Bearer secret"},
                    timeout=5,
                )
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)

    def test_not_found(self):
        response = requests.get(f"{self.base_url}/nope", timeout=5)
        self.assertEqual(response.status_code, 404)

    def test_create_task_confirms_via_verify(self):
        created = {
            "id": "abc123",
            "globalTaskId": "abc123",
            "title": "Buy milk",
            "status": "UNCHECKED",
            "categoryId": "list1",
        }
        mock_client = Mock()
        mock_client.create_task.return_value = created
        mock_client.verify_task.return_value = created
        with patch("anydown.api._bootstrap_client", return_value=(mock_client, None)):
            response = requests.post(
                f"{self.base_url}/tasks",
                json={"title": "Buy milk", "note": "from alexa", "category_id": "list1"},
                timeout=5,
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["confirmed"])
        self.assertEqual(payload["id"], "abc123")
        self.assertEqual(payload["title"], "Buy milk")
        mock_client.create_task.assert_called_once_with(
            "Buy milk",
            category_id="list1",
            note="from alexa",
            labels=None,
        )
        mock_client.verify_task.assert_called_once_with("abc123")

    def test_create_task_requires_title(self):
        response = requests.post(f"{self.base_url}/api/tasks", json={"note": "x"}, timeout=5)
        self.assertEqual(response.status_code, 400)
        self.assertIn("title", response.json()["error"])

    def test_create_task_verify_missed(self):
        mock_client = Mock()
        mock_client.create_task.return_value = {"id": "abc123", "globalTaskId": "abc123", "title": "Buy milk"}
        mock_client.verify_task.return_value = None
        with patch("anydown.api._bootstrap_client", return_value=(mock_client, None)):
            response = requests.post(f"{self.base_url}/tasks", json={"title": "Buy milk"}, timeout=5)
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.json()["ok"])

    def test_get_task_confirmed(self):
        verified = {
            "id": "abc123",
            "globalTaskId": "abc123",
            "title": "Buy milk",
            "status": "UNCHECKED",
            "categoryId": "list1",
        }
        mock_client = Mock()
        mock_client.verify_task.return_value = verified
        with patch("anydown.api._bootstrap_client", return_value=(mock_client, None)):
            response = requests.get(f"{self.base_url}/tasks/abc123", timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "abc123")
        self.assertTrue(response.json()["confirmed"])

    def test_get_task_missing(self):
        mock_client = Mock()
        mock_client.verify_task.return_value = None
        with patch("anydown.api._bootstrap_client", return_value=(mock_client, None)):
            response = requests.get(f"{self.base_url}/api/tasks/missing", timeout=5)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["ok"])
