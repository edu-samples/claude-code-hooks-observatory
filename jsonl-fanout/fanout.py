#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
JSONL Fan-Out Daemon — ZeroMQ-inspired PUB/SUB over Unix sockets.

Reads JSONL lines from stdin and fans them out to all connected Unix socket
subscribers. Subscribers connect/disconnect freely; slow or dead subscribers
are dropped immediately (no buffering), matching ZeroMQ PUB socket semantics.

Why this design:
    Observatory servers emit JSONL to stdout. Multiple tools need the same
    stream (query scripts, jq filters, dashboards). Shell-level FIFO fan-out
    is fragile (blocking writers, manual cleanup, fixed subscribers). This
    daemon enables dynamic PUB/SUB: pipe in, subscribe from anywhere.

Architecture:
    server.py ──stdout──> tee -a log ──> fanout.py ──> subscriber 1
                                                   ├──> subscriber 2
                                                   └──> subscriber N

Usage:
    # Pipe from any observatory server
    ./tcp-observatory/server.py | tee -a obs.log | ./jsonl-fanout/fanout.py

    # Custom socket path
    ./jsonl-fanout/fanout.py --socket /tmp/my-fanout.sock

    # Subscribe (any of these work)
    ./jsonl-fanout/subscribe.py
    socat - UNIX-CONNECT:/tmp/claude-fanout.sock

EDU_NOTES:
    Non-blocking stdin: We read raw bytes and manually split on '\\n' because
    non-blocking reads aren't line-aligned. A single os.read() may return half
    a line, multiple lines, or anything in between. The stdin_buf accumulates
    bytes until we find complete lines.

    ZeroMQ PUB semantics: A production system would add per-client write buffers
    with high-water marks. We drop immediately because (a) it's simpler, (b) it
    matches the "observe, don't block" philosophy, and (c) it teaches why ZeroMQ
    made this same design choice.
"""

from __future__ import annotations

import argparse
import os
import selectors
import socket
import sys
import time

DEFAULT_SOCKET = "/tmp/claude-fanout.sock"
ENV_SOCKET = "CLAUDE_FANOUT_SOCKET"


def fan_out(line: bytes, subscribers: list[socket.socket]) -> int:
    """Send a line to all subscribers, dropping any that fail.

    Returns the number of subscribers that were dropped.
    This is the same pattern as OutputManager._write_to_clients()
    in unix-socket-observatory/server.py:203-214.
    """
    dead: list[socket.socket] = []
    for client in subscribers:
        try:
            client.sendall(line)
        except (BrokenPipeError, OSError):
            dead.append(client)
    for client in dead:
        subscribers.remove(client)
        try:
            client.close()
        except OSError:
            pass
    return len(dead)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="JSONL fan-out daemon — reads stdin, fans out to Unix socket subscribers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    ./tcp-observatory/server.py | tee -a obs.log | ./fanout.py
    echo '{"test":1}' | ./fanout.py --socket /tmp/test.sock
    ./fanout.py --stats  # print subscriber stats to stderr

Socket precedence: --socket > $CLAUDE_FANOUT_SOCKET > /tmp/claude-fanout.sock
        """,
    )
    parser.add_argument(
        "--socket",
        type=str,
        default=None,
        help=f"Socket path (default: ${ENV_SOCKET} or {DEFAULT_SOCKET})",
    )
    parser.add_argument(
        "--mode",
        type=lambda x: int(x, 8),
        default=0o660,
        help="Socket file permissions in octal (default: 0660)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        default=False,
        help="Print periodic subscriber stats to stderr",
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
    """Run the fan-out event loop.

    Two file descriptors are monitored via selectors:
    1. stdin — incoming JSONL lines to fan out
    2. listener socket — new subscriber connections
    """
    args = parse_args()
    socket_path = get_socket_path(args.socket)

    # Clean up stale socket from a previous crash
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.setblocking(False)
    listener.bind(socket_path)
    os.chmod(socket_path, args.mode)
    listener.listen(128)

    subscribers: list[socket.socket] = []

    # Non-blocking stdin for the selectors event loop
    os.set_blocking(sys.stdin.fileno(), False)

    sel = selectors.DefaultSelector()
    sel.register(sys.stdin.fileno(), selectors.EVENT_READ, data="stdin")
    sel.register(listener, selectors.EVENT_READ, data="listener")

    sys.stderr.write(f"JSONL fan-out listening on {socket_path}\n")
    sys.stderr.write(f"Socket permissions: {oct(args.mode)}\n")
    sys.stderr.write("Waiting for stdin...\n")

    # EDU_NOTE: Non-blocking reads aren't line-aligned. A single os.read()
    # may return half a line, two lines, or anything in between. We accumulate
    # bytes in stdin_buf and split on '\n' to extract complete lines.
    stdin_buf = b""
    lines_total = 0
    last_stats = time.monotonic()

    try:
        while True:
            events = sel.select(timeout=1.0)

            for key, mask in events:
                if key.data == "stdin":
                    chunk = os.read(sys.stdin.fileno(), 65536)
                    if not chunk:
                        # EOF — upstream pipe closed, shut down
                        sys.stderr.write(
                            f"\nstdin EOF after {lines_total} lines, shutting down\n"
                        )
                        return
                    stdin_buf += chunk
                    while b"\n" in stdin_buf:
                        line, stdin_buf = stdin_buf.split(b"\n", 1)
                        complete_line = line + b"\n"
                        dropped = fan_out(complete_line, subscribers)
                        lines_total += 1
                        if dropped:
                            sys.stderr.write(
                                f"Dropped {dropped} subscriber(s) "
                                f"({len(subscribers)} remaining)\n"
                            )

                elif key.data == "listener":
                    try:
                        client, _ = listener.accept()
                        client.setblocking(False)
                        subscribers.append(client)
                        sys.stderr.write(
                            f"Subscriber connected ({len(subscribers)} total)\n"
                        )
                    except (OSError, BlockingIOError):
                        pass

            # Periodic stats
            if args.stats:
                now = time.monotonic()
                if now - last_stats >= 5.0:
                    sys.stderr.write(
                        f"[stats] subscribers={len(subscribers)} "
                        f"lines={lines_total}\n"
                    )
                    last_stats = now

    except KeyboardInterrupt:
        sys.stderr.write(f"\nInterrupted after {lines_total} lines\n")
    finally:
        sel.close()
        for client in subscribers:
            try:
                client.close()
            except OSError:
                pass
        listener.close()
        if os.path.exists(socket_path):
            os.unlink(socket_path)


if __name__ == "__main__":
    main()
