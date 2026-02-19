#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///
"""
JSONL Fan-Out Daemon — Tests

Subprocess-based integration tests following the pattern from
unix-socket-observatory/test_server_selectors.py.

Usage:
    uv run --script test_fanout.py        # Run all tests
    uv run --script test_fanout.py -v     # Verbose output
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest


FANOUT_SCRIPT = os.path.join(os.path.dirname(__file__), "fanout.py")
SUBSCRIBE_SCRIPT = os.path.join(os.path.dirname(__file__), "subscribe.py")


def make_temp_socket_path() -> str:
    """Create a temporary path for a Unix socket."""
    return tempfile.mktemp(suffix=".sock", prefix="test-fanout-")


def wait_for_socket(path: str, timeout: float = 5.0) -> None:
    """Wait for a socket file to appear."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return
        time.sleep(0.05)
    pytest.fail(f"Socket {path} did not appear within {timeout}s")


def connect_subscriber(path: str) -> socket.socket:
    """Connect a raw subscriber socket."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect(path)
    return sock


def recv_lines(sock: socket.socket, count: int, timeout: float = 5.0) -> list[str]:
    """Receive exactly `count` newline-terminated lines from a socket."""
    buf = b""
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while len(lines) < count and time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            lines.append(line.decode())
    return lines


class TestFanoutIntegration:
    """Integration tests running fanout.py as a subprocess."""

    def test_single_subscriber_receives_lines(self) -> None:
        """One subscriber receives all lines sent to stdin."""
        sock_path = make_temp_socket_path()
        proc = subprocess.Popen(
            ["uv", "run", "--script", FANOUT_SCRIPT, "--socket", sock_path],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wait_for_socket(sock_path)
            sub = connect_subscriber(sock_path)

            # Give subscriber time to register
            time.sleep(0.1)

            # Send 3 JSONL lines
            for i in range(3):
                line = json.dumps({"seq": i}) + "\n"
                proc.stdin.write(line.encode())
                proc.stdin.flush()

            lines = recv_lines(sub, 3)
            sub.close()

            assert len(lines) == 3
            for i, line in enumerate(lines):
                data = json.loads(line)
                assert data["seq"] == i
        finally:
            proc.stdin.close()
            proc.wait(timeout=5)
            if os.path.exists(sock_path):
                os.unlink(sock_path)

    def test_multiple_subscribers_each_get_every_line(self) -> None:
        """All subscribers receive a copy of every line."""
        sock_path = make_temp_socket_path()
        proc = subprocess.Popen(
            ["uv", "run", "--script", FANOUT_SCRIPT, "--socket", sock_path],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wait_for_socket(sock_path)

            subs = [connect_subscriber(sock_path) for _ in range(3)]
            time.sleep(0.1)

            # Send 2 lines
            for i in range(2):
                line = json.dumps({"n": i}) + "\n"
                proc.stdin.write(line.encode())
                proc.stdin.flush()

            # Each subscriber should get both lines
            for sub in subs:
                lines = recv_lines(sub, 2)
                assert len(lines) == 2
                assert json.loads(lines[0])["n"] == 0
                assert json.loads(lines[1])["n"] == 1
                sub.close()
        finally:
            proc.stdin.close()
            proc.wait(timeout=5)
            if os.path.exists(sock_path):
                os.unlink(sock_path)

    def test_subscriber_disconnect_doesnt_crash_daemon(self) -> None:
        """Disconnecting a subscriber doesn't affect the daemon or others."""
        sock_path = make_temp_socket_path()
        proc = subprocess.Popen(
            ["uv", "run", "--script", FANOUT_SCRIPT, "--socket", sock_path],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wait_for_socket(sock_path)

            sub1 = connect_subscriber(sock_path)
            sub2 = connect_subscriber(sock_path)
            time.sleep(0.1)

            # Send first line — both receive it
            proc.stdin.write(b'{"phase":"before"}\n')
            proc.stdin.flush()
            assert len(recv_lines(sub1, 1)) == 1
            assert len(recv_lines(sub2, 1)) == 1

            # Disconnect sub1
            sub1.close()
            time.sleep(0.1)

            # Send second line — only sub2 receives it
            proc.stdin.write(b'{"phase":"after"}\n')
            proc.stdin.flush()
            lines = recv_lines(sub2, 1)
            assert len(lines) == 1
            assert json.loads(lines[0])["phase"] == "after"
            sub2.close()
        finally:
            proc.stdin.close()
            proc.wait(timeout=5)
            if os.path.exists(sock_path):
                os.unlink(sock_path)

    def test_stdin_eof_shuts_down_cleanly(self) -> None:
        """Closing stdin causes the daemon to exit cleanly."""
        sock_path = make_temp_socket_path()
        proc = subprocess.Popen(
            ["uv", "run", "--script", FANOUT_SCRIPT, "--socket", sock_path],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wait_for_socket(sock_path)

            # Send one line then close stdin
            proc.stdin.write(b'{"done":true}\n')
            proc.stdin.flush()
            proc.stdin.close()

            # Daemon should exit within a reasonable time
            retcode = proc.wait(timeout=10)
            assert retcode == 0

            # Socket should be cleaned up
            time.sleep(0.1)
            assert not os.path.exists(sock_path)
        finally:
            if proc.poll() is None:
                proc.kill()
            if os.path.exists(sock_path):
                os.unlink(sock_path)

    def test_end_to_end_with_subscribe_script(self) -> None:
        """fanout.py + subscribe.py composed end-to-end."""
        sock_path = make_temp_socket_path()
        fanout = subprocess.Popen(
            ["uv", "run", "--script", FANOUT_SCRIPT, "--socket", sock_path],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wait_for_socket(sock_path)

            subscriber = subprocess.Popen(
                ["uv", "run", "--script", SUBSCRIBE_SCRIPT, "--socket", sock_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # Give subscribe.py time to connect
            time.sleep(0.3)

            # Send lines through fanout
            for i in range(3):
                line = json.dumps({"e2e": i}) + "\n"
                fanout.stdin.write(line.encode())
                fanout.stdin.flush()

            # Close fanout stdin to trigger shutdown chain
            time.sleep(0.3)
            fanout.stdin.close()
            fanout.wait(timeout=10)

            # subscribe.py should receive all lines and exit when connection closes
            stdout, _ = subscriber.communicate(timeout=10)
            lines = stdout.decode().strip().split("\n")
            assert len(lines) == 3
            for i, line in enumerate(lines):
                assert json.loads(line)["e2e"] == i
        finally:
            if fanout.poll() is None:
                fanout.kill()
            if subscriber.poll() is None:
                subscriber.kill()
            if os.path.exists(sock_path):
                os.unlink(sock_path)

    def test_no_subscribers_doesnt_block(self) -> None:
        """Lines sent with no subscribers connected are silently discarded."""
        sock_path = make_temp_socket_path()
        proc = subprocess.Popen(
            ["uv", "run", "--script", FANOUT_SCRIPT, "--socket", sock_path],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wait_for_socket(sock_path)

            # Send lines with no subscribers
            for i in range(5):
                proc.stdin.write(json.dumps({"lost": i}).encode() + b"\n")
                proc.stdin.flush()

            # Now connect a subscriber — should only get new lines
            time.sleep(0.1)
            sub = connect_subscriber(sock_path)
            time.sleep(0.1)

            proc.stdin.write(b'{"found":true}\n')
            proc.stdin.flush()

            lines = recv_lines(sub, 1)
            assert len(lines) == 1
            assert json.loads(lines[0])["found"] is True
            sub.close()
        finally:
            proc.stdin.close()
            proc.wait(timeout=5)
            if os.path.exists(sock_path):
                os.unlink(sock_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
