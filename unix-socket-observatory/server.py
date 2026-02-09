#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml", "pygments"]
# ///
"""
Claude Code Hooks Observatory - Unix Socket Server (HTTPServer-based)

A transparent server for observing Claude Code hook events via Unix domain
sockets. Uses Python's HTTPServer with AF_UNIX override for familiar patterns.

Why Unix sockets?
    No TCP port to configure or collide. Filesystem permissions control access.
    SO_PEERCRED tells you exactly which process connected (PID, UID, GID).

Usage:
    ./server.py                                    # Default: /tmp/claude-observatory.sock
    ./server.py --socket /run/user/1000/hooks.sock # Custom socket path
    ./server.py --output-socket /tmp/obs-out.sock  # Multi-reader output
    ./server.py --tee                              # Output to both stdout and output socket
    ./server.py --pretty-yaml                      # Human-readable YAML
    CLAUDE_UNIX_HOOK_WATCHER=/tmp/my.sock ./server.py  # Path from env
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml
from pygments import highlight
from pygments.lexers import YamlLexer
from pygments.formatters import Terminal256Formatter

DEFAULT_SOCKET = "/tmp/claude-observatory.sock"
ENV_SOCKET = "CLAUDE_UNIX_HOOK_WATCHER"

# Module-level output mode (set from CLI args in main)
# Values: "jsonl" (default), "pretty-json", "pretty-yaml"
_output_mode = "jsonl"


class _MultilineYamlDumper(yaml.SafeDumper):
    """YAML dumper that renders strings containing newlines as block scalars (|)."""
    pass


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_MultilineYamlDumper.add_representer(str, _str_representer)


def get_timestamp() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_peer_creds(sock: socket.socket) -> tuple[int, int, int] | None:
    """Get peer credentials (pid, uid, gid) from a Unix socket connection.

    Uses SO_PEERCRED on Linux, LOCAL_PEERCRED on macOS. These are
    kernel-verified - the connecting process cannot forge them.

    Returns None on unsupported platforms or errors.
    """
    # === SO_PEERCRED (Linux) ===
    # The kernel fills a struct ucred {pid_t pid; uid_t uid; gid_t gid;}
    # when we call getsockopt with SO_PEERCRED. This tells us exactly
    # which process is on the other end of this socket.
    try:
        SO_PEERCRED = 17  # Linux constant
        cred = sock.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", cred)
        # pid=0 means the socket isn't connected or isn't AF_UNIX
        if pid > 0:
            return (pid, uid, gid)
    except (OSError, struct.error):
        pass

    # === LOCAL_PEERCRED (macOS) ===
    # macOS uses a different socket option and struct layout.
    try:
        LOCAL_PEERCRED = 0x001  # macOS constant
        # struct xucred on macOS: uint version, uid_t uid, short ngroups, gid_t groups[16]
        buf = sock.getsockopt(socket.SOL_LOCAL, LOCAL_PEERCRED, struct.calcsize("IIh16I"))
        _, uid, ngroups, *groups = struct.unpack("IIh16I", buf)
        gid = groups[0] if ngroups > 0 else -1
        # macOS LOCAL_PEERCRED doesn't provide PID directly
        # LOCAL_PEERPID (0x002) is a separate option
        try:
            LOCAL_PEERPID = 0x002
            pid_buf = sock.getsockopt(socket.SOL_LOCAL, LOCAL_PEERPID, struct.calcsize("I"))
            pid = struct.unpack("I", pid_buf)[0]
        except (OSError, AttributeError):
            pid = -1
        return (pid, uid, gid)
    except (OSError, struct.error, AttributeError):
        pass

    return None


def enrich_payload(
    payload: dict[str, Any], event: str, peer_creds: tuple[int, int, int] | None
) -> dict[str, Any]:
    """Add metadata fields (prefixed with _) to the payload."""
    result: dict[str, Any] = {
        "_ts": get_timestamp(),
        "_event": event,
    }
    if peer_creds is not None:
        pid, uid, gid = peer_creds
        result["_peer_pid"] = pid
        result["_peer_uid"] = uid
        result["_peer_gid"] = gid
    result.update(payload)
    return result


def format_event(data: dict[str, Any]) -> str:
    """Format a single event in the configured output format. Returns string."""
    match _output_mode:
        case "pretty-yaml":
            yaml_text = yaml.dump(
                data, Dumper=_MultilineYamlDumper,
                default_flow_style=False, sort_keys=False,
            )
            if sys.stdout.isatty():
                return (
                    "\033[90m---\033[0m\n"
                    + highlight(yaml_text, YamlLexer(), Terminal256Formatter())
                )
            else:
                return "---\n" + yaml_text
        case "pretty-json":
            return json.dumps(data, indent=2) + "\n"
        case _:
            return json.dumps(data, separators=(",", ":")) + "\n"


class OutputManager:
    """Manages where output goes: stdout, output socket, or both (tee).

    The output socket lets multiple readers connect and receive the JSONL
    stream without needing shell-level tee + FIFOs.
    """

    def __init__(self, output_socket_path: str | None, tee: bool) -> None:
        self._tee = tee
        self._output_socket_path = output_socket_path
        self._listener: socket.socket | None = None
        self._clients: list[socket.socket] = []

        if output_socket_path:
            # Clean up stale socket file from a previous crash
            if os.path.exists(output_socket_path):
                os.unlink(output_socket_path)
            self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._listener.setblocking(False)
            self._listener.bind(output_socket_path)
            self._listener.listen(128)
            sys.stderr.write(f"Output socket: {output_socket_path}\n")

    def accept_pending(self) -> None:
        """Accept any pending output socket connections (non-blocking)."""
        if self._listener is None:
            return
        try:
            client, _ = self._listener.accept()
            client.setblocking(False)
            self._clients.append(client)
            sys.stderr.write(f"Output reader connected ({len(self._clients)} total)\n")
        except BlockingIOError:
            pass  # No pending connections

    def write(self, line: str) -> None:
        """Write a line to the configured outputs."""
        if self._output_socket_path and not self._tee:
            # Output socket only - don't write to stdout
            self._write_to_clients(line)
        elif self._output_socket_path and self._tee:
            # Both stdout and output socket
            sys.stdout.write(line)
            sys.stdout.flush()
            self._write_to_clients(line)
        else:
            # Default: stdout only
            sys.stdout.write(line)
            sys.stdout.flush()

    def _write_to_clients(self, line: str) -> None:
        """Send data to all connected output socket clients."""
        dead: list[socket.socket] = []
        data = line.encode()
        for client in self._clients:
            try:
                client.sendall(data)
            except (BrokenPipeError, OSError):
                dead.append(client)
        for client in dead:
            self._clients.remove(client)
            client.close()

    def cleanup(self) -> None:
        """Close all connections and unlink output socket."""
        for client in self._clients:
            try:
                client.close()
            except OSError:
                pass
        if self._listener:
            self._listener.close()
        if self._output_socket_path and os.path.exists(self._output_socket_path):
            os.unlink(self._output_socket_path)


class UnixHTTPServer(HTTPServer):
    """HTTPServer that listens on a Unix domain socket instead of TCP.

    We override three things:
    1. address_family → AF_UNIX (use filesystem path, not IP:port)
    2. server_bind() → bind to path + set permissions
    3. service_actions() → poll for output socket connections
    """

    address_family = socket.AF_UNIX
    # Increase from default 5 so parallel hooks queue instead of being refused
    request_queue_size = 128

    def __init__(
        self, socket_path: str, handler: type, mode: int, output_manager: OutputManager
    ) -> None:
        self.socket_path = socket_path
        self._socket_mode = mode
        self.output_manager = output_manager
        # Clean up stale socket from a previous crash
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        # HTTPServer.__init__ calls server_bind() and server_activate()
        super().__init__(socket_path, handler)

    def server_bind(self) -> None:
        """Bind to Unix socket path and set filesystem permissions."""
        self.socket.bind(self.server_address)
        os.chmod(self.socket_path, self._socket_mode)

    def server_close(self) -> None:
        """Clean up: close socket and remove socket file."""
        super().server_close()
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        self.output_manager.cleanup()

    def service_actions(self) -> None:
        """Called by serve_forever() between handling requests.

        We use this to poll for new output socket connections without
        needing a separate thread.
        """
        self.output_manager.accept_pending()


class HookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for hook events over Unix socket."""

    server: UnixHTTPServer  # type narrowing

    def log_message(self, format: str, *args: Any) -> None:
        """Redirect HTTP logs to stderr to keep output clean."""
        sys.stderr.write(f"[HTTP] {args[0]} {args[1]} {args[2]}\n")

    def do_POST(self) -> None:
        """Handle POST /hook?event=<EventType> requests."""
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        event = query.get("event", ["Unknown"])[0]

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"_raw": body.decode("utf-8", errors="replace")}

        # Get peer credentials from the Unix socket connection
        peer_creds = get_peer_creds(self.request)

        enriched = enrich_payload(payload, event, peer_creds)
        formatted = format_event(enriched)
        self.server.output_manager.write(formatted)

        # Return empty 200 (no-op response)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        """Handle GET requests (health check)."""
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            response = json.dumps({"status": "ok"})
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response.encode())
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Claude Code Hooks Observatory (Unix Socket) - observe hook events",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    ./server.py                                    # Default socket path
    ./server.py --socket /run/user/1000/hooks.sock # Custom path
    ./server.py --output-socket /tmp/obs-out.sock  # Multi-reader output
    ./server.py --tee                              # stdout + output socket
    ./server.py --pretty-yaml                      # YAML output
    ./server.py --mode 0600                        # Owner-only access

Socket precedence: --socket > $CLAUDE_UNIX_HOOK_WATCHER > /tmp/claude-observatory.sock
        """,
    )
    parser.add_argument(
        "--socket",
        type=str,
        default=None,
        help=f"Socket path to listen on (default: ${ENV_SOCKET} or {DEFAULT_SOCKET})",
    )
    parser.add_argument(
        "--output-socket",
        type=str,
        default=None,
        help="Path for output socket (readers connect here to receive JSONL)",
    )
    parser.add_argument(
        "--tee",
        action="store_true",
        default=False,
        help="Output to both stdout and output socket (requires --output-socket)",
    )
    parser.add_argument(
        "--mode",
        type=lambda x: int(x, 8),
        default=0o660,
        help="Socket file permissions in octal (default: 0660)",
    )
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument(
        "--pretty-json",
        action="store_true",
        default=False,
        help="Output indented multiline JSON instead of compact JSONL",
    )
    fmt.add_argument(
        "--pretty-yaml",
        action="store_true",
        default=False,
        help="Output YAML with block scalars for multiline strings",
    )
    return parser.parse_args()


def get_socket_path(cli_path: str | None) -> str:
    """Determine socket path with precedence: CLI > env > default."""
    if cli_path is not None:
        return cli_path
    env_path = os.environ.get(ENV_SOCKET)
    if env_path:
        return env_path
    return DEFAULT_SOCKET


def main() -> None:
    """Start the observatory server."""
    global _output_mode
    args = parse_args()
    socket_path = get_socket_path(args.socket)

    if args.pretty_yaml:
        _output_mode = "pretty-yaml"
    elif args.pretty_json:
        _output_mode = "pretty-json"

    if args.tee and not args.output_socket:
        sys.stderr.write("Error: --tee requires --output-socket\n")
        sys.exit(1)

    output_manager = OutputManager(args.output_socket, args.tee)

    server = UnixHTTPServer(socket_path, HookHandler, args.mode, output_manager)

    sys.stderr.write(f"Claude Code Hooks Observatory (Unix Socket) listening on {socket_path}\n")
    sys.stderr.write(f"Socket permissions: {oct(args.mode)}\n")
    if args.output_socket:
        sys.stderr.write(f"Output socket: {args.output_socket}")
        if args.tee:
            sys.stderr.write(" (tee: stdout + socket)")
        sys.stderr.write("\n")
    sys.stderr.write("Press Ctrl+C to stop\n\n")

    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        sys.stderr.write("\nShutting down...\n")
        server.server_close()


if __name__ == "__main__":
    main()
