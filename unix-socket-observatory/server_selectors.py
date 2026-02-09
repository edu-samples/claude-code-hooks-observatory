#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml", "pygments"]
# ///
"""
Claude Code Hooks Observatory - Unix Socket Server (Raw Selectors)

Same functionality as server.py, but built from raw sockets and the
selectors module instead of HTTPServer. This shows what HTTPServer hides.

Why this file exists:
    server.py uses HTTPServer which handles socket creation, accept loops,
    and HTTP parsing behind clean abstractions. This file does all of that
    manually so you can see every step.

Usage:
    ./server_selectors.py                                    # Default socket
    ./server_selectors.py --socket /run/user/1000/hooks.sock # Custom path
    ./server_selectors.py --output-socket /tmp/obs-out.sock  # Multi-reader
    ./server_selectors.py --pretty-yaml                      # YAML output
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import socket
import struct
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml
from pygments import highlight
from pygments.lexers import YamlLexer
from pygments.formatters import Terminal256Formatter

DEFAULT_SOCKET = "/tmp/claude-observatory.sock"
ENV_SOCKET = "CLAUDE_UNIX_HOOK_WATCHER"

# Module-level output mode (set from CLI args in main)
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

    Identical to server.py's implementation - duplicated intentionally
    so each file is standalone and readable without cross-file imports.
    """
    try:
        SO_PEERCRED = 17
        cred = sock.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", cred)
        return (pid, uid, gid)
    except (OSError, struct.error):
        pass

    try:
        LOCAL_PEERCRED = 0x001
        buf = sock.getsockopt(socket.SOL_LOCAL, LOCAL_PEERCRED, struct.calcsize("IIh16I"))
        _, uid, ngroups, *groups = struct.unpack("IIh16I", buf)
        gid = groups[0] if ngroups > 0 else -1
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
    """Format a single event in the configured output format."""
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


# === HTTP Parsing ===
# HTTPServer does this automatically. Here we do it by hand to show
# what the "parse HTTP request" step actually involves.


def parse_http_request(data: bytes) -> tuple[str, str, str, dict[str, str]]:
    """Parse a raw HTTP request into (method, path, body, headers).

    HTTP/1.1 requests look like:
        POST /hook?event=PreToolUse HTTP/1.1\\r\\n
        Content-Type: application/json\\r\\n
        Content-Length: 42\\r\\n
        \\r\\n
        {"tool_name": "Bash"}

    The \\r\\n\\r\\n separates headers from body.
    """
    # Split headers from body at the blank line
    header_end = data.find(b"\r\n\r\n")
    if header_end == -1:
        # No complete headers yet - treat entire thing as headers
        header_section = data.decode("utf-8", errors="replace")
        body = ""
    else:
        header_section = data[:header_end].decode("utf-8", errors="replace")
        body = data[header_end + 4:].decode("utf-8", errors="replace")

    lines = header_section.split("\r\n")

    # First line: "POST /hook?event=PreToolUse HTTP/1.1"
    request_line = lines[0] if lines else ""
    parts = request_line.split(" ", 2)
    method = parts[0] if len(parts) >= 1 else ""
    path = parts[1] if len(parts) >= 2 else "/"

    # Remaining lines are headers: "Key: Value"
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ": " in line:
            key, value = line.split(": ", 1)
            headers[key.lower()] = value

    return method, path, body, headers


def build_http_response(status: int, body: str = "") -> bytes:
    """Build a raw HTTP/1.1 response.

    HTTP responses look like:
        HTTP/1.1 200 OK\\r\\n
        Content-Type: application/json\\r\\n
        Content-Length: 0\\r\\n
        \\r\\n
    """
    reason = {200: "OK", 404: "Not Found"}.get(status, "Unknown")
    response = f"HTTP/1.1 {status} {reason}\r\n"
    response += "Content-Type: application/json\r\n"
    response += f"Content-Length: {len(body)}\r\n"
    response += "\r\n"
    response += body
    return response.encode()


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Claude Code Hooks Observatory (Unix Socket, Selectors) - raw socket impl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    ./server_selectors.py                                    # Default socket
    ./server_selectors.py --socket /tmp/my.sock              # Custom path
    ./server_selectors.py --output-socket /tmp/obs-out.sock  # Multi-reader
    ./server_selectors.py --pretty-yaml                      # YAML output

Socket precedence: --socket > $CLAUDE_UNIX_HOOK_WATCHER > /tmp/claude-observatory.sock
        """,
    )
    parser.add_argument(
        "--socket",
        type=str,
        default=None,
        help=f"Socket path (default: ${ENV_SOCKET} or {DEFAULT_SOCKET})",
    )
    parser.add_argument(
        "--output-socket",
        type=str,
        default=None,
        help="Output socket path for readers",
    )
    parser.add_argument(
        "--tee",
        action="store_true",
        default=False,
        help="Output to both stdout and output socket",
    )
    parser.add_argument(
        "--mode",
        type=lambda x: int(x, 8),
        default=0o660,
        help="Socket file permissions in octal (default: 0660)",
    )
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--pretty-json", action="store_true", default=False)
    fmt.add_argument("--pretty-yaml", action="store_true", default=False)
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
    """Run the event loop.

    === WHY selectors? ===
    The selectors module lets us monitor multiple sockets in a single thread.
    When any socket has data ready, select() returns it. No threads needed.

    We register callbacks for each socket:
    - Input socket → accept new connections, read HTTP, respond
    - Output socket → accept reader connections
    """
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

    # === Create the main (input) socket ===
    # This is what HTTPServer does in __init__ + server_bind()
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    input_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    input_sock.setblocking(False)
    input_sock.bind(socket_path)
    os.chmod(socket_path, args.mode)
    input_sock.listen(5)

    # === Create the output socket (optional) ===
    output_sock: socket.socket | None = None
    output_clients: list[socket.socket] = []
    if args.output_socket:
        if os.path.exists(args.output_socket):
            os.unlink(args.output_socket)
        output_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        output_sock.setblocking(False)
        output_sock.bind(args.output_socket)
        output_sock.listen(5)
        sys.stderr.write(f"Output socket: {args.output_socket}\n")

    # === Register with selectors ===
    # DefaultSelector picks the best available: epoll (Linux), kqueue (macOS),
    # or select (fallback). All work the same way from our perspective.
    sel = selectors.DefaultSelector()
    sel.register(input_sock, selectors.EVENT_READ, data="input_listener")
    if output_sock:
        sel.register(output_sock, selectors.EVENT_READ, data="output_listener")

    def write_output(line: str) -> None:
        """Send formatted output to configured destinations."""
        if args.output_socket and not args.tee:
            _write_to_clients(line)
        elif args.output_socket and args.tee:
            sys.stdout.write(line)
            sys.stdout.flush()
            _write_to_clients(line)
        else:
            sys.stdout.write(line)
            sys.stdout.flush()

    def _write_to_clients(line: str) -> None:
        """Send data to all connected output readers."""
        dead: list[socket.socket] = []
        data = line.encode()
        for client in output_clients:
            try:
                client.sendall(data)
            except (BrokenPipeError, OSError):
                dead.append(client)
        for client in dead:
            output_clients.remove(client)
            client.close()

    def handle_input_connection(conn: socket.socket) -> None:
        """Read HTTP request, process hook event, send HTTP response.

        This is what BaseHTTPRequestHandler does automatically.
        Here we do each step manually.
        """
        # Read the full request (hook payloads are small, so one recv suffices)
        try:
            raw = conn.recv(65536)
        except OSError:
            conn.close()
            return

        if not raw:
            conn.close()
            return

        # Parse the raw HTTP bytes into components
        method, path, body, headers = parse_http_request(raw)

        if method == "GET" and path == "/health":
            response_body = json.dumps({"status": "ok"})
            conn.sendall(build_http_response(200, response_body))
            conn.close()
            return

        if method != "POST":
            conn.sendall(build_http_response(404))
            conn.close()
            return

        # Extract event type from query string
        parsed = urlparse(path)
        query = parse_qs(parsed.query)
        event = query.get("event", ["Unknown"])[0]

        # If body is shorter than Content-Length, read more
        expected_len = int(headers.get("content-length", "0"))
        while len(body.encode()) < expected_len:
            try:
                more = conn.recv(65536)
                if not more:
                    break
                body += more.decode("utf-8", errors="replace")
            except OSError:
                break

        # Parse JSON payload
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"_raw": body}

        # Get peer credentials
        peer_creds = get_peer_creds(conn)

        # Enrich and output
        enriched = enrich_payload(payload, event, peer_creds)
        formatted = format_event(enriched)
        write_output(formatted)

        # Send HTTP 200 response (no-op)
        conn.sendall(build_http_response(200))
        conn.close()

    sys.stderr.write(
        f"Claude Code Hooks Observatory (Selectors) listening on {socket_path}\n"
    )
    sys.stderr.write(f"Socket permissions: {oct(args.mode)}\n")
    sys.stderr.write("Press Ctrl+C to stop\n\n")

    # === Main event loop ===
    # This replaces HTTPServer.serve_forever(). The selector blocks until
    # any registered socket has data, then we dispatch to the right handler.
    try:
        while True:
            events = sel.select(timeout=1.0)
            for key, mask in events:
                if key.data == "input_listener":
                    # New input connection (like HTTPServer.get_request())
                    try:
                        conn, _ = input_sock.accept()
                        handle_input_connection(conn)
                    except OSError:
                        pass

                elif key.data == "output_listener":
                    # New output reader connecting
                    try:
                        client, _ = output_sock.accept()  # type: ignore[union-attr]
                        client.setblocking(False)
                        output_clients.append(client)
                        sys.stderr.write(
                            f"Output reader connected ({len(output_clients)} total)\n"
                        )
                    except (OSError, BlockingIOError):
                        pass

    except KeyboardInterrupt:
        sys.stderr.write("\nShutting down...\n")
    finally:
        sel.close()
        input_sock.close()
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        for client in output_clients:
            try:
                client.close()
            except OSError:
                pass
        if output_sock:
            output_sock.close()
            if args.output_socket and os.path.exists(args.output_socket):
                os.unlink(args.output_socket)


if __name__ == "__main__":
    main()
