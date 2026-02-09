#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml", "pygments"]
# ///
"""
Claude Code Hooks Observatory - Server

A transparent REST server for observing Claude Code hook events.
Outputs JSONL to stdout, returns empty 200 responses (no-op).

Why single-file?
    A developer should understand the entire server in under 10 minutes.
    No jumping between modules. Copy, fork, modify freely.

Usage:
    ./server.py                              # Default: 127.0.0.1:23518
    ./server.py --pretty-json                # Human-readable indented JSON
    ./server.py --pretty-yaml                # Human-readable YAML (multiline strings)
    ./server.py --port 9999                  # Custom port
    ./server.py --bind 0.0.0.0               # Bind to all interfaces
    CLAUDE_REST_HOOK_WATCHER=8080 ./server.py  # Port from env
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml
from pygments import highlight
from pygments.lexers import YamlLexer
from pygments.formatters import Terminal256Formatter

DEFAULT_PORT = 23518
DEFAULT_BIND = "127.0.0.1"
ENV_PORT = "CLAUDE_REST_HOOK_WATCHER"

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


def enrich_payload(payload: dict[str, Any], event: str, client: str) -> dict[str, Any]:
    """Add metadata fields (prefixed with _) to the payload."""
    return {
        "_ts": get_timestamp(),
        "_event": event,
        "_client": client,
        **payload,
    }


def output_event(data: dict[str, Any]) -> None:
    """Output a single event to stdout in the configured format."""
    match _output_mode:
        case "pretty-yaml":
            yaml_text = yaml.dump(
                data, Dumper=_MultilineYamlDumper,
                default_flow_style=False, sort_keys=False,
            )
            if sys.stdout.isatty():
                print("\033[90m---\033[0m")
                print(highlight(yaml_text, YamlLexer(), Terminal256Formatter()),
                      end="", flush=True)
            else:
                print("---")
                print(yaml_text, end="", flush=True)
        case "pretty-json":
            print(json.dumps(data, indent=2), flush=True)
        case _:
            print(json.dumps(data, separators=(",", ":")), flush=True)


class HookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for hook events."""

    def log_message(self, format: str, *args: Any) -> None:
        """Redirect HTTP logs to stderr to keep stdout clean for JSONL."""
        sys.stderr.write(f"[HTTP] {args[0]} {args[1]} {args[2]}\n")

    def do_POST(self) -> None:
        """Handle POST /hook?event=<EventType> requests."""
        # Parse URL and query params
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        # Extract event type from query param
        event = query.get("event", ["Unknown"])[0]

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Parse JSON payload
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"_raw": body.decode("utf-8", errors="replace")}

        # Get client address
        client = self.client_address[0]

        # Enrich and output JSONL
        enriched = enrich_payload(payload, event, client)
        output_event(enriched)

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
        description="Claude Code Hooks Observatory - observe hook events in real-time",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    ./server.py                              # Default: 127.0.0.1:23518
    ./server.py --pretty-json                # Human-readable indented JSON
    ./server.py --pretty-yaml                # Human-readable YAML (multiline strings)
    ./server.py --port 9999                  # Custom port
    ./server.py --bind 0.0.0.0               # Bind to all interfaces
    CLAUDE_REST_HOOK_WATCHER=8080 ./server.py  # Port from env

Port precedence: --port > $CLAUDE_REST_HOOK_WATCHER > 23518
        """,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port to listen on (default: ${ENV_PORT} or {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--bind",
        type=str,
        default=DEFAULT_BIND,
        help=f"Address to bind to (default: {DEFAULT_BIND})",
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


def get_port(cli_port: int | None) -> int:
    """Determine port with precedence: CLI > env > default."""
    if cli_port is not None:
        return cli_port
    env_port = os.environ.get(ENV_PORT)
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            sys.stderr.write(f"Warning: Invalid {ENV_PORT}='{env_port}', using default\n")
    return DEFAULT_PORT


def main() -> None:
    """Start the observatory server."""
    global _output_mode
    args = parse_args()
    port = get_port(args.port)
    bind = args.bind
    if args.pretty_yaml:
        _output_mode = "pretty-yaml"
    elif args.pretty_json:
        _output_mode = "pretty-json"

    server = HTTPServer((bind, port), HookHandler)

    # Startup message to stderr (keeps stdout clean for JSONL)
    sys.stderr.write(f"Claude Code Hooks Observatory listening on {bind}:{port}\n")
    sys.stderr.write("Press Ctrl+C to stop\n")
    sys.stderr.write("\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nShutting down...\n")
        server.shutdown()


if __name__ == "__main__":
    main()
