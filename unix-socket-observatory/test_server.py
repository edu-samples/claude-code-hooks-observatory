#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest", "pyyaml", "pygments"]
# ///
"""
Claude Code Hooks Observatory (Unix Socket) - Tests

Why?
    These tests demonstrate how to verify Unix socket hook server behavior
    without running Claude Code. Each test is self-contained and readable.

Usage:
    uv run --script test_server.py        # Run all tests
    uv run --script test_server.py -v     # Verbose output
"""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
from datetime import datetime
from threading import Thread
from typing import Any, Generator
from unittest.mock import patch

import pytest

# Import from server module
sys.path.insert(0, os.path.dirname(__file__))
from server import (
    HookHandler,
    UnixHTTPServer,
    OutputManager,
    enrich_payload,
    format_event,
    get_peer_creds,
    get_socket_path,
    get_timestamp,
    DEFAULT_SOCKET,
    ENV_SOCKET,
)


def make_temp_socket_path() -> str:
    """Create a temporary path for a Unix socket.

    We use tempfile.mktemp (not mkstemp) because we need a path that
    doesn't exist yet - the socket will be created by the server.
    """
    return tempfile.mktemp(suffix=".sock", prefix="test-observatory-")


def make_request(
    socket_path: str, method: str, path: str, body: str = ""
) -> tuple[int, str]:
    """Make an HTTP request over a Unix socket.

    stdlib http.client doesn't support Unix sockets, so we build
    the raw HTTP request and parse the response manually.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(socket_path)

    # Build raw HTTP request
    request = f"{method} {path} HTTP/1.1\r\n"
    request += "Host: localhost\r\n"
    if body:
        request += "Content-Type: application/json\r\n"
        request += f"Content-Length: {len(body)}\r\n"
    request += "\r\n"
    request += body

    sock.sendall(request.encode())

    # Read response
    response = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
        # Check if we got a complete response (headers + body)
        if b"\r\n\r\n" in response:
            header_end = response.index(b"\r\n\r\n")
            headers = response[:header_end].decode()
            # Find Content-Length
            content_length = 0
            for line in headers.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    content_length = int(line.split(":")[1].strip())
            body_start = header_end + 4
            if len(response) >= body_start + content_length:
                break

    sock.close()

    # Parse status code from first line: "HTTP/1.1 200 OK"
    status_line = response.split(b"\r\n")[0].decode()
    status_code = int(status_line.split(" ")[1])

    # Parse body
    body_start = response.index(b"\r\n\r\n") + 4
    response_body = response[body_start:].decode()

    return status_code, response_body


class TestEnrichPayload:
    """Test payload enrichment with metadata."""

    def test_adds_timestamp(self) -> None:
        """Enriched payload includes _ts field."""
        result = enrich_payload({}, "TestEvent", None)
        assert "_ts" in result
        datetime.fromisoformat(result["_ts"].replace("Z", "+00:00"))

    def test_adds_event_type(self) -> None:
        """Enriched payload includes _event from query param."""
        result = enrich_payload({}, "PreToolUse", None)
        assert result["_event"] == "PreToolUse"

    def test_adds_peer_credentials(self) -> None:
        """Enriched payload includes _peer_pid, _peer_uid, _peer_gid when available."""
        result = enrich_payload({}, "TestEvent", (1234, 1000, 1000))
        assert result["_peer_pid"] == 1234
        assert result["_peer_uid"] == 1000
        assert result["_peer_gid"] == 1000

    def test_omits_peer_when_none(self) -> None:
        """No peer fields when credentials unavailable."""
        result = enrich_payload({}, "TestEvent", None)
        assert "_peer_pid" not in result
        assert "_peer_uid" not in result
        assert "_peer_gid" not in result

    def test_preserves_original_payload(self) -> None:
        """Original payload fields are preserved."""
        original = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        result = enrich_payload(original, "PreToolUse", (1, 1000, 1000))
        assert result["tool_name"] == "Bash"
        assert result["tool_input"] == {"command": "ls"}

    def test_metadata_prefix_with_creds(self) -> None:
        """Metadata fields are prefixed with underscore."""
        result = enrich_payload({"data": "value"}, "Event", (1, 1000, 1000))
        metadata_keys = [k for k in result.keys() if k.startswith("_")]
        assert set(metadata_keys) == {"_ts", "_event", "_peer_pid", "_peer_uid", "_peer_gid"}


class TestOutputFormat:
    """Test output format."""

    def test_jsonl_is_single_line(self) -> None:
        """Default format is compact single-line JSON."""
        data = {"key": "value", "nested": {"a": 1}}
        output = format_event(data)
        assert output.count("\n") == 1

    def test_jsonl_is_valid_json(self) -> None:
        """Output can be parsed as JSON."""
        data = {"key": "value", "number": 42}
        output = format_event(data).strip()
        parsed = json.loads(output)
        assert parsed == data

    def test_jsonl_is_compact(self) -> None:
        """No unnecessary whitespace in default output."""
        data = {"a": 1, "b": 2}
        output = format_event(data).strip()
        assert " " not in output


class TestSocketConfiguration:
    """Test socket path precedence: CLI > env > default."""

    def test_default_socket(self) -> None:
        """Uses default path when no override provided."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop(ENV_SOCKET, None)
            assert get_socket_path(None) == DEFAULT_SOCKET

    def test_env_var_socket(self) -> None:
        """Environment variable overrides default."""
        with patch.dict(os.environ, {ENV_SOCKET: "/tmp/custom.sock"}):
            assert get_socket_path(None) == "/tmp/custom.sock"

    def test_cli_overrides_env(self) -> None:
        """CLI argument overrides environment variable."""
        with patch.dict(os.environ, {ENV_SOCKET: "/tmp/env.sock"}):
            assert get_socket_path("/tmp/cli.sock") == "/tmp/cli.sock"


class TestGetTimestamp:
    """Test timestamp generation."""

    def test_returns_iso_format(self) -> None:
        """Timestamp is in ISO format."""
        ts = get_timestamp()
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed is not None

    def test_includes_timezone(self) -> None:
        """Timestamp includes timezone info."""
        ts = get_timestamp()
        assert "+00:00" in ts or ts.endswith("Z")


class TestPeerCredentials:
    """Test SO_PEERCRED extraction via a real Unix socket pair."""

    def test_returns_credentials_on_unix_socket(self) -> None:
        """get_peer_creds returns (pid, uid, gid) on Linux Unix sockets."""
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        path = make_temp_socket_path()
        try:
            server_sock.bind(path)
            server_sock.listen(1)

            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(path)
            conn, _ = server_sock.accept()

            creds = get_peer_creds(conn)
            if creds is not None:
                pid, uid, gid = creds
                # PID should be our process
                assert pid == os.getpid()
                # UID should be our user
                assert uid == os.getuid()
                # GID should be our group
                assert gid == os.getgid()
            # On platforms without SO_PEERCRED, creds is None (acceptable)

            conn.close()
            client.close()
        finally:
            server_sock.close()
            if os.path.exists(path):
                os.unlink(path)

    def test_returns_none_on_tcp_socket(self) -> None:
        """get_peer_creds returns None on non-Unix sockets."""
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        assert get_peer_creds(tcp_sock) is None
        tcp_sock.close()


class TestHookEndpoint:
    """Test the /hook endpoint via Unix socket."""

    @pytest.fixture
    def server(self) -> Generator[tuple[UnixHTTPServer, str], None, None]:
        """Create a test server on a temporary Unix socket."""
        path = make_temp_socket_path()
        output_mgr = OutputManager(None, False)
        srv = UnixHTTPServer(path, HookHandler, 0o660, output_mgr)
        thread = Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()
        yield srv, path
        srv.shutdown()
        srv.server_close()

    def test_pre_tool_use_returns_empty_200(
        self, server: tuple[UnixHTTPServer, str]
    ) -> None:
        """PreToolUse: returns 200 with empty body (no-op)."""
        _, path = server
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        status, body = make_request(path, "POST", "/hook?event=PreToolUse", payload)
        assert status == 200
        assert body == ""

    def test_post_tool_use_returns_empty_200(
        self, server: tuple[UnixHTTPServer, str]
    ) -> None:
        """PostToolUse: returns 200 with empty body (no-op)."""
        _, path = server
        payload = json.dumps({"tool_name": "Read", "tool_response": {"content": "..."}})
        status, body = make_request(path, "POST", "/hook?event=PostToolUse", payload)
        assert status == 200
        assert body == ""

    def test_session_start_returns_empty_200(
        self, server: tuple[UnixHTTPServer, str]
    ) -> None:
        """SessionStart: returns 200 with empty body (no-op)."""
        _, path = server
        payload = json.dumps({"source": "startup", "model": "claude-sonnet-4-5"})
        status, body = make_request(path, "POST", "/hook?event=SessionStart", payload)
        assert status == 200
        assert body == ""

    def test_session_end_returns_empty_200(
        self, server: tuple[UnixHTTPServer, str]
    ) -> None:
        """SessionEnd: returns 200 with empty body (no-op)."""
        _, path = server
        payload = json.dumps({"reason": "logout"})
        status, body = make_request(path, "POST", "/hook?event=SessionEnd", payload)
        assert status == 200
        assert body == ""

    def test_notification_returns_empty_200(
        self, server: tuple[UnixHTTPServer, str]
    ) -> None:
        """Notification: returns 200 with empty body (no-op)."""
        _, path = server
        payload = json.dumps({"message": "test", "notification_type": "permission_prompt"})
        status, body = make_request(path, "POST", "/hook?event=Notification", payload)
        assert status == 200
        assert body == ""

    def test_stop_returns_empty_200(
        self, server: tuple[UnixHTTPServer, str]
    ) -> None:
        """Stop: returns 200 with empty body (no-op)."""
        _, path = server
        payload = json.dumps({"stop_hook_active": True})
        status, body = make_request(path, "POST", "/hook?event=Stop", payload)
        assert status == 200
        assert body == ""

    def test_permission_request_returns_empty_200(
        self, server: tuple[UnixHTTPServer, str]
    ) -> None:
        """PermissionRequest: returns 200 with empty body (no-op)."""
        _, path = server
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}})
        status, body = make_request(path, "POST", "/hook?event=PermissionRequest", payload)
        assert status == 200
        assert body == ""

    def test_user_prompt_submit_returns_empty_200(
        self, server: tuple[UnixHTTPServer, str]
    ) -> None:
        """UserPromptSubmit: returns 200 with empty body (no-op)."""
        _, path = server
        payload = json.dumps({"prompt": "Hello, Claude!"})
        status, body = make_request(path, "POST", "/hook?event=UserPromptSubmit", payload)
        assert status == 200
        assert body == ""


class TestHealthEndpoint:
    """Test the /health endpoint via Unix socket."""

    @pytest.fixture
    def server(self) -> Generator[tuple[UnixHTTPServer, str], None, None]:
        """Create a test server on a temporary Unix socket."""
        path = make_temp_socket_path()
        output_mgr = OutputManager(None, False)
        srv = UnixHTTPServer(path, HookHandler, 0o660, output_mgr)
        thread = Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()
        yield srv, path
        srv.shutdown()
        srv.server_close()

    def test_health_returns_ok(
        self, server: tuple[UnixHTTPServer, str]
    ) -> None:
        """GET /health returns status ok."""
        _, path = server
        status, body = make_request(path, "GET", "/health")
        assert status == 200
        parsed = json.loads(body)
        assert parsed == {"status": "ok"}


class TestOutputManager:
    """Test OutputManager stdout/socket/tee modes."""

    def test_stdout_by_default(self) -> None:
        """Without output socket, writes to stdout."""
        mgr = OutputManager(None, False)
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            mgr.write('{"test":1}\n')
            assert mock_stdout.getvalue() == '{"test":1}\n'
        mgr.cleanup()

    def test_output_socket_replaces_stdout(self) -> None:
        """With output socket (no tee), stdout gets nothing."""
        out_path = make_temp_socket_path()
        mgr = OutputManager(out_path, tee=False)
        try:
            # Connect a reader
            reader = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            reader.connect(out_path)
            mgr.accept_pending()

            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                mgr.write('{"test":1}\n')
                # stdout should be empty
                assert mock_stdout.getvalue() == ""

            # Reader should have received the data
            data = reader.recv(4096).decode()
            assert '{"test":1}' in data
            reader.close()
        finally:
            mgr.cleanup()

    def test_tee_mode(self) -> None:
        """With --tee, writes to both stdout and output socket."""
        out_path = make_temp_socket_path()
        mgr = OutputManager(out_path, tee=True)
        try:
            reader = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            reader.connect(out_path)
            mgr.accept_pending()

            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                mgr.write('{"test":1}\n')
                assert mock_stdout.getvalue() == '{"test":1}\n'

            data = reader.recv(4096).decode()
            assert '{"test":1}' in data
            reader.close()
        finally:
            mgr.cleanup()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
