"""Tests for the anydown HTTP API."""

import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import requests

from anydown.api import AnydownAPIHandler, agent_export_available, read_agent_export


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
