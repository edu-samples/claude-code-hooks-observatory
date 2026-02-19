#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
JSONL Fan-Out Subscriber — connect to fanout.py and print lines.

Connects to the fan-out daemon's Unix socket and prints every received
JSONL line to stdout. Compose with jq, grep, or any line-oriented tool.

Usage:
    ./subscribe.py                                          # Default socket
    ./subscribe.py --socket /tmp/my-fanout.sock             # Custom path
    ./subscribe.py | jq 'select(._event == "PreToolUse")'   # Filter events
    ./subscribe.py | jq '.tool_name'                        # Extract fields

You don't need this script — socat works too:
    socat - UNIX-CONNECT:/tmp/claude-fanout.sock
"""

from __future__ import annotations

import argparse
import os
import socket
import sys

DEFAULT_SOCKET = "/tmp/claude-fanout.sock"
ENV_SOCKET = "CLAUDE_FANOUT_SOCKET"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Subscribe to JSONL fan-out stream",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    ./subscribe.py                                         # Print all events
    ./subscribe.py | jq 'select(._event == "PreToolUse")'  # Filter
    ./subscribe.py | jq -r '.tool_name // empty'           # Extract fields

Socket precedence: --socket > $CLAUDE_FANOUT_SOCKET > /tmp/claude-fanout.sock
        """,
    )
    parser.add_argument(
        "--socket",
        type=str,
        default=None,
        help=f"Socket path (default: ${ENV_SOCKET} or {DEFAULT_SOCKET})",
    )
    return parser.parse_args()


def get_socket_path(cli_path: str | None) -> str:
    """Determine socket path: CLI > env > default."""
    if cli_path is not None:
        return cli_path
    env_path = os.environ.get(ENV_SOCKET)
    if env_path:
        return env_path
    return DEFAULT_SOCKET


def main() -> None:
    """Connect and print lines until the connection closes."""
    args = parse_args()
    socket_path = get_socket_path(args.socket)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(socket_path)
    except (FileNotFoundError, ConnectionRefusedError) as e:
        sys.stderr.write(f"Cannot connect to {socket_path}: {e}\n")
        sys.stderr.write("Is fanout.py running?\n")
        sys.exit(1)

    sys.stderr.write(f"Connected to {socket_path}\n")

    buf = b""
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                sys.stdout.buffer.write(line + b"\n")
                sys.stdout.buffer.flush()
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:
        pass
    finally:
        sock.close()
        sys.stderr.write("Disconnected\n")


if __name__ == "__main__":
    main()
