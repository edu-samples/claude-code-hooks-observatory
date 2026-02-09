#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest", "pyyaml", "pygments"]
# ///
"""
Claude Code Hooks Observatory - Tests

Why?
    These tests demonstrate how to verify hook server behavior without
    running Claude Code. Each test is self-contained and readable.
    Copy any test as a starting point for testing your custom endpoints.

Usage:
    uv run pytest                           # Run all tests
    uv run pytest -v                        # Verbose output
    uv run pytest test_server.py::TestHookEndpoint  # Run specific class
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime
from http.server import HTTPServer
from threading import Thread
from typing import Any, Generator
from unittest.mock import patch

import pytest

# Import from server module
sys.path.insert(0, os.path.dirname(__file__))
from server import (
    HookHandler,
    enrich_payload,
    get_port,
    get_timestamp,
    output_event,
    DEFAULT_PORT,
    ENV_PORT,
)


class TestEnrichPayload:
    """Test payload enrichment with metadata."""

    def test_adds_timestamp(self) -> None:
        """Enriched payload includes _ts field."""
        result = enrich_payload({}, "TestEvent", "127.0.0.1")
        assert "_ts" in result
        # Verify ISO format
        datetime.fromisoformat(result["_ts"].replace("Z", "+00:00"))

    def test_adds_event_type(self) -> None:
        """Enriched payload includes _event from query param."""
        result = enrich_payload({}, "PreToolUse", "127.0.0.1")
        assert result["_event"] == "PreToolUse"

    def test_adds_client_ip(self) -> None:
        """Enriched payload includes _client field."""
        result = enrich_payload({}, "TestEvent", "192.168.1.1")
        assert result["_client"] == "192.168.1.1"

    def test_preserves_original_payload(self) -> None:
        """Original payload fields are preserved."""
        original = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        result = enrich_payload(original, "PreToolUse", "127.0.0.1")
        assert result["tool_name"] == "Bash"
        assert result["tool_input"] == {"command": "ls"}

    def test_metadata_prefix(self) -> None:
        """Metadata fields are prefixed with underscore."""
        result = enrich_payload({"data": "value"}, "Event", "127.0.0.1")
        metadata_keys = [k for k in result.keys() if k.startswith("_")]
        assert set(metadata_keys) == {"_ts", "_event", "_client"}


class TestOutputJSONL:
    """Test JSONL output format."""

    def test_output_is_single_line(self) -> None:
        """Payload is compacted to single line."""
        data = {"key": "value", "nested": {"a": 1, "b": 2}}
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            output_event(data)
            output = mock_stdout.getvalue()
        assert output.count("\n") == 1
        assert "\n" not in output.rstrip("\n")

    def test_output_is_valid_json(self) -> None:
        """Output can be parsed as JSON."""
        data = {"key": "value", "number": 42}
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            output_event(data)
            output = mock_stdout.getvalue().strip()
        parsed = json.loads(output)
        assert parsed == data

    def test_output_is_compact(self) -> None:
        """No unnecessary whitespace in output."""
        data = {"a": 1, "b": 2}
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            output_event(data)
            output = mock_stdout.getvalue().strip()
        # Compact format uses no spaces after separators
        assert " " not in output


class TestPortConfiguration:
    """Test port precedence: CLI > env > default."""

    def test_default_port(self) -> None:
        """Uses default port when no override provided."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop(ENV_PORT, None)
            assert get_port(None) == DEFAULT_PORT

    def test_env_var_port(self) -> None:
        """Environment variable overrides default."""
        with patch.dict(os.environ, {ENV_PORT: "9999"}):
            assert get_port(None) == 9999

    def test_cli_overrides_env(self) -> None:
        """CLI argument overrides environment variable."""
        with patch.dict(os.environ, {ENV_PORT: "9999"}):
            assert get_port(8888) == 8888

    def test_invalid_env_uses_default(self) -> None:
        """Invalid env var falls back to default."""
        with patch.dict(os.environ, {ENV_PORT: "not-a-number"}):
            # Capture stderr to avoid test output pollution
            with patch("sys.stderr", new_callable=io.StringIO):
                assert get_port(None) == DEFAULT_PORT


class TestGetTimestamp:
    """Test timestamp generation."""

    def test_returns_iso_format(self) -> None:
        """Timestamp is in ISO format."""
        ts = get_timestamp()
        # Should be parseable as ISO format
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed is not None

    def test_includes_timezone(self) -> None:
        """Timestamp includes timezone info."""
        ts = get_timestamp()
        # UTC timestamps end with +00:00 or Z
        assert "+00:00" in ts or ts.endswith("Z")


class TestHookEndpoint:
    """Test the /hook endpoint for each event type."""

    @pytest.fixture
    def server(self) -> Generator[HTTPServer, None, None]:
        """Create a test server on a random port."""
        server = HTTPServer(("127.0.0.1", 0), HookHandler)
        thread = Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        yield server
        server.shutdown()

    def make_request(
        self, server: HTTPServer, event: str, payload: dict[str, Any]
    ) -> tuple[int, bytes]:
        """Make a POST request to the hook endpoint."""
        import http.client

        host, port = server.server_address
        conn = http.client.HTTPConnection(host, port)
        body = json.dumps(payload)
        conn.request(
            "POST",
            f"/hook?event={event}",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        return response.status, response.read()

    def test_pre_tool_use_returns_empty_200(self, server: HTTPServer) -> None:
        """PreToolUse: returns 200 with empty body (no-op)."""
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        status, body = self.make_request(server, "PreToolUse", payload)
        assert status == 200
        assert body == b""

    def test_post_tool_use_returns_empty_200(self, server: HTTPServer) -> None:
        """PostToolUse: returns 200 with empty body (no-op)."""
        payload = {"tool_name": "Read", "tool_response": {"content": "..."}}
        status, body = self.make_request(server, "PostToolUse", payload)
        assert status == 200
        assert body == b""

    def test_session_start_returns_empty_200(self, server: HTTPServer) -> None:
        """SessionStart: returns 200 with empty body (no-op)."""
        payload = {"source": "startup", "model": "claude-sonnet-4-5"}
        status, body = self.make_request(server, "SessionStart", payload)
        assert status == 200
        assert body == b""

    def test_session_end_returns_empty_200(self, server: HTTPServer) -> None:
        """SessionEnd: returns 200 with empty body (no-op)."""
        payload = {"reason": "logout"}
        status, body = self.make_request(server, "SessionEnd", payload)
        assert status == 200
        assert body == b""

    def test_user_prompt_submit_returns_empty_200(self, server: HTTPServer) -> None:
        """UserPromptSubmit: returns 200 with empty body (no-op)."""
        payload = {"prompt": "Hello, Claude!"}
        status, body = self.make_request(server, "UserPromptSubmit", payload)
        assert status == 200
        assert body == b""

    def test_stop_returns_empty_200(self, server: HTTPServer) -> None:
        """Stop: returns 200 with empty body (no-op)."""
        payload = {"stop_hook_active": True}
        status, body = self.make_request(server, "Stop", payload)
        assert status == 200
        assert body == b""

    def test_permission_request_returns_empty_200(self, server: HTTPServer) -> None:
        """PermissionRequest: returns 200 with empty body (no-op)."""
        payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}
        status, body = self.make_request(server, "PermissionRequest", payload)
        assert status == 200
        assert body == b""

    def test_notification_returns_empty_200(self, server: HTTPServer) -> None:
        """Notification: returns 200 with empty body (no-op)."""
        payload = {"message": "Permission needed", "notification_type": "permission_prompt"}
        status, body = self.make_request(server, "Notification", payload)
        assert status == 200
        assert body == b""


class TestHealthEndpoint:
    """Test the /health endpoint."""

    @pytest.fixture
    def server(self) -> Generator[HTTPServer, None, None]:
        """Create a test server on a random port."""
        server = HTTPServer(("127.0.0.1", 0), HookHandler)
        thread = Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        yield server
        server.shutdown()

    def test_health_returns_ok(self, server: HTTPServer) -> None:
        """GET /health returns status ok."""
        import http.client

        host, port = server.server_address
        conn = http.client.HTTPConnection(host, port)
        conn.request("GET", "/health")
        response = conn.getresponse()
        assert response.status == 200
        body = json.loads(response.read())
        assert body == {"status": "ok"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
