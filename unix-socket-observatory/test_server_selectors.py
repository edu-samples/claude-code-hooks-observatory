#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest", "pyyaml", "pygments"]
# ///
"""
Claude Code Hooks Observatory (Unix Socket, Selectors) - Tests

Tests the raw selectors-based server by running it as a subprocess.
This mirrors how the server is actually used (as a standalone process).

Usage:
    uv run --script test_server_selectors.py        # Run all tests
    uv run --script test_server_selectors.py -v     # Verbose output
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from typing import Generator

import pytest

# Also import shared functions for unit testing
sys.path.insert(0, os.path.dirname(__file__))
from server_selectors import (
    parse_http_request,
    build_http_response,
    enrich_payload,
    get_peer_creds,
    get_socket_path,
    get_timestamp,
    DEFAULT_SOCKET,
    ENV_SOCKET,
)


def make_temp_socket_path() -> str:
    """Create a temporary path for a Unix socket."""
    return tempfile.mktemp(suffix=".sock", prefix="test-selectors-")


def make_request(socket_path: str, method: str, path: str, body: str = "") -> tuple[int, str]:
    """Make an HTTP request over a Unix socket."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect(socket_path)

    request = f"{method} {path} HTTP/1.1\r\n"
    request += "Host: localhost\r\n"
    if body:
        request += "Content-Type: application/json\r\n"
        request += f"Content-Length: {len(body)}\r\n"
    request += "\r\n"
    request += body

    sock.sendall(request.encode())

    response = b""
    while True:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        response += chunk
        if b"\r\n\r\n" in response:
            header_end = response.index(b"\r\n\r\n")
            headers = response[:header_end].decode()
            content_length = 0
            for line in headers.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    content_length = int(line.split(":")[1].strip())
            body_start = header_end + 4
            if len(response) >= body_start + content_length:
                break

    sock.close()

    status_line = response.split(b"\r\n")[0].decode()
    status_code = int(status_line.split(" ")[1])
    body_start = response.index(b"\r\n\r\n") + 4
    response_body = response[body_start:].decode()

    return status_code, response_body


class TestParseHttpRequest:
    """Test manual HTTP request parsing."""

    def test_parses_post_request(self) -> None:
        """Correctly parses a POST request with body."""
        raw = (
            b"POST /hook?event=PreToolUse HTTP/1.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 21\r\n"
            b"\r\n"
            b'{"tool_name": "Bash"}'
        )
        method, path, body, headers = parse_http_request(raw)
        assert method == "POST"
        assert path == "/hook?event=PreToolUse"
        assert body == '{"tool_name": "Bash"}'
        assert headers["content-type"] == "application/json"
        assert headers["content-length"] == "21"

    def test_parses_get_request(self) -> None:
        """Correctly parses a GET request."""
        raw = b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n"
        method, path, body, headers = parse_http_request(raw)
        assert method == "GET"
        assert path == "/health"
        assert body == ""

    def test_handles_empty_body(self) -> None:
        """POST with no body."""
        raw = b"POST /hook?event=Stop HTTP/1.1\r\nContent-Length: 0\r\n\r\n"
        method, path, body, headers = parse_http_request(raw)
        assert method == "POST"
        assert body == ""


class TestBuildHttpResponse:
    """Test HTTP response construction."""

    def test_200_empty_body(self) -> None:
        """200 OK with empty body."""
        resp = build_http_response(200)
        assert b"HTTP/1.1 200 OK" in resp
        assert b"Content-Length: 0" in resp

    def test_200_with_body(self) -> None:
        """200 OK with JSON body."""
        resp = build_http_response(200, '{"status":"ok"}')
        assert b"HTTP/1.1 200 OK" in resp
        assert b"Content-Length: 15" in resp
        assert resp.endswith(b'{"status":"ok"}')

    def test_404(self) -> None:
        """404 Not Found."""
        resp = build_http_response(404)
        assert b"HTTP/1.1 404 Not Found" in resp


class TestEnrichPayload:
    """Test payload enrichment (same as server.py)."""

    def test_adds_timestamp_and_event(self) -> None:
        result = enrich_payload({}, "TestEvent", None)
        assert "_ts" in result
        assert result["_event"] == "TestEvent"

    def test_adds_peer_creds(self) -> None:
        result = enrich_payload({}, "TestEvent", (1234, 1000, 1000))
        assert result["_peer_pid"] == 1234


class TestSocketConfiguration:
    """Test socket path precedence."""

    def test_default(self) -> None:
        with patch_env_clear():
            assert get_socket_path(None) == DEFAULT_SOCKET

    def test_env_override(self) -> None:
        os.environ[ENV_SOCKET] = "/tmp/env-test.sock"
        try:
            assert get_socket_path(None) == "/tmp/env-test.sock"
        finally:
            del os.environ[ENV_SOCKET]

    def test_cli_override(self) -> None:
        os.environ[ENV_SOCKET] = "/tmp/env-test.sock"
        try:
            assert get_socket_path("/tmp/cli.sock") == "/tmp/cli.sock"
        finally:
            del os.environ[ENV_SOCKET]


class TestSelectorsServerIntegration:
    """Integration tests running the selectors server as a subprocess."""

    @pytest.fixture
    def server_process(self) -> Generator[tuple[subprocess.Popen, str], None, None]:
        """Start the selectors server as a subprocess."""
        path = make_temp_socket_path()
        server_script = os.path.join(os.path.dirname(__file__), "server_selectors.py")

        proc = subprocess.Popen(
            ["uv", "run", "--script", server_script, "--socket", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for socket file to appear
        for _ in range(50):
            if os.path.exists(path):
                break
            time.sleep(0.1)
        else:
            proc.kill()
            pytest.fail("Server socket did not appear within 5 seconds")

        yield proc, path

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if os.path.exists(path):
            os.unlink(path)

    def test_hook_returns_200(
        self, server_process: tuple[subprocess.Popen, str]
    ) -> None:
        """POST /hook returns 200 (no-op)."""
        _, path = server_process
        payload = json.dumps({"tool_name": "Bash"})
        status, body = make_request(path, "POST", "/hook?event=PreToolUse", payload)
        assert status == 200
        assert body == ""

    def test_health_returns_ok(
        self, server_process: tuple[subprocess.Popen, str]
    ) -> None:
        """GET /health returns status ok."""
        _, path = server_process
        status, body = make_request(path, "GET", "/health")
        assert status == 200
        assert json.loads(body) == {"status": "ok"}

    def test_outputs_enriched_jsonl(
        self, server_process: tuple[subprocess.Popen, str]
    ) -> None:
        """Server outputs enriched JSONL to stdout."""
        proc, path = server_process
        payload = json.dumps({"tool_name": "Edit"})
        make_request(path, "POST", "/hook?event=PostToolUse", payload)

        # Give server a moment to flush stdout
        time.sleep(0.2)

        # Terminate and read stdout
        proc.terminate()
        stdout, _ = proc.communicate(timeout=5)

        # Should contain enriched JSONL
        lines = stdout.decode().strip().split("\n")
        assert len(lines) >= 1
        event = json.loads(lines[0])
        assert event["_event"] == "PostToolUse"
        assert event["tool_name"] == "Edit"
        assert "_ts" in event

    def test_multiple_events(
        self, server_process: tuple[subprocess.Popen, str]
    ) -> None:
        """Server handles multiple sequential requests."""
        _, path = server_process
        events = ["SessionStart", "PreToolUse", "PostToolUse", "SessionEnd"]
        for event in events:
            payload = json.dumps({"event_data": event})
            status, _ = make_request(path, "POST", f"/hook?event={event}", payload)
            assert status == 200


# Helper for clearing env
from contextlib import contextmanager

@contextmanager
def patch_env_clear():
    old = os.environ.pop(ENV_SOCKET, None)
    try:
        yield
    finally:
        if old is not None:
            os.environ[ENV_SOCKET] = old


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
