#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Query observatory log files by hook event type.

Reads JSONL from log files (or stdin) and filters by event type, tool name,
or session. Outputs human-readable indented JSON (default) or compact JSONL
for piping into jq or other tools.

Examples:

    # Show all PreToolUse events (human-readable)
    ./scripts/query-hooks.py PreToolUse

    # Multiple event types
    ./scripts/query-hooks.py PreToolUse PostToolUse

    # JSONL output piped to jq for further filtering
    ./scripts/query-hooks.py PreToolUse --jsonl | jq '.tool_input.command // empty'

    # Filter by tool name
    ./scripts/query-hooks.py PreToolUse --tool Bash

    # Which sessions are waiting for user input right now?
    ./scripts/query-hooks.py --waiting

    # All historical waiting events as JSONL
    ./scripts/query-hooks.py --waiting=all --jsonl | jq '.reason'

    # Count events by type
    ./scripts/query-hooks.py --jsonl | jq -r '._event' | sort | uniq -c | sort -rn
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DEFAULT_LOG_DIR = Path("/tmp/claude/observatory")

# Events and notification types that signal "waiting for user input"
WAITING_EVENTS = {"PermissionRequest", "Notification"}
WAITING_NOTIFICATION_TYPES = {"permission_prompt", "idle_prompt", "elicitation_dialog"}

# Events that signal a session is actively working (clears "waiting" state)
ACTIVE_EVENTS = {
    "PreToolUse", "PostToolUse", "PostToolUseFailure",
    "UserPromptSubmit", "SubagentStart",
}


def is_waiting_event(event: dict) -> bool:
    """Does this event indicate a session needs user attention?"""
    etype = event.get("_event", "")
    if etype == "PermissionRequest":
        return True
    if etype == "Notification":
        return event.get("notification_type") in WAITING_NOTIFICATION_TYPES
    return False


def waiting_reason(event: dict) -> str:
    """Human-readable reason why the session is waiting."""
    etype = event.get("_event", "")
    if etype == "PermissionRequest":
        tool = event.get("tool_name", "?")
        return f"permission needed: {tool}"
    if etype == "Notification":
        ntype = event.get("notification_type", "")
        msg = event.get("message", "")
        match ntype:
            case "idle_prompt":
                return "idle — waiting for input"
            case "permission_prompt":
                return msg or "permission dialog"
            case "elicitation_dialog":
                return msg or "question for user"
            case _:
                return msg or ntype
    return etype


def project_name(cwd: str) -> str:
    """Extract a short project name from cwd path."""
    if not cwd:
        return "?"
    parts = Path(cwd).parts
    # Use last 1-2 meaningful path components
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1] if parts else "?"


def time_ago(ts_str: str) -> str:
    """Human-readable time since timestamp (e.g. '5m ago', '2h ago')."""
    try:
        # Parse ISO timestamp (with or without timezone)
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        secs = int(delta.total_seconds())
        if secs < 0:
            return "just now"
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h {(secs % 3600) // 60}m ago"
        return f"{secs // 86400}d ago"
    except (ValueError, TypeError):
        return "?"


def run_waiting(args: argparse.Namespace, sources: list[Path] | None) -> None:
    """Handle --waiting mode: find sessions needing user attention."""
    mode = args.waiting  # "recent" or "all"

    # Per-session tracking: {session_id: {last_wait_event, last_active_ts, ...}}
    sessions: dict[str, dict] = {}
    all_waiting: list[dict] = []

    for line in iter_lines(sources):
        line = line.strip()
        if not line or line[0] != "{":
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        sid = event.get("session_id", "")
        if not sid:
            continue

        ts = event.get("_ts", "")
        etype = event.get("_event", "")

        # Initialize session record
        if sid not in sessions:
            sessions[sid] = {
                "last_wait": None,
                "last_active_ts": "",
                "cwd": event.get("cwd", ""),
            }

        rec = sessions[sid]
        # Always update cwd to latest
        if event.get("cwd"):
            rec["cwd"] = event["cwd"]

        if is_waiting_event(event):
            enriched = {
                "_ts": ts,
                "_event": etype,
                "session_id": sid,
                "reason": waiting_reason(event),
                "cwd": event.get("cwd", ""),
                "project": project_name(event.get("cwd", "")),
                "tool_name": event.get("tool_name"),
                "notification_type": event.get("notification_type"),
                "message": event.get("message"),
            }
            # Remove None values for cleaner output
            enriched = {k: v for k, v in enriched.items() if v is not None}
            rec["last_wait"] = enriched
            rec["last_active_ts"] = ""  # reset: now waiting
            if mode == "all":
                all_waiting.append(enriched)
        elif etype in ACTIVE_EVENTS:
            rec["last_active_ts"] = ts  # session resumed work

    if mode == "all":
        _output_all_waiting(all_waiting, args.jsonl)
    else:
        _output_recent_waiting(sessions, args.jsonl)


def _output_all_waiting(events: list[dict], jsonl: bool) -> None:
    """Output all historical waiting events."""
    if not events:
        print("No waiting events found.", file=sys.stderr)
        return
    for ev in events:
        if jsonl:
            print(json.dumps(ev, separators=(",", ":")))
        else:
            print(json.dumps(ev, indent=2))


def _output_recent_waiting(
    sessions: dict[str, dict], jsonl: bool
) -> None:
    """Output sessions currently waiting (last signal is a wait, not resumed)."""
    waiting = []
    for sid, rec in sessions.items():
        w = rec["last_wait"]
        if w is None:
            continue
        # If session had activity AFTER the wait event, it's no longer waiting
        wait_ts = w.get("_ts", "")
        if rec["last_active_ts"] and rec["last_active_ts"] > wait_ts:
            continue
        waiting.append(w)

    # Sort by timestamp descending (most recent wait first)
    waiting.sort(key=lambda e: e.get("_ts", ""), reverse=True)

    if not waiting:
        print("No sessions currently waiting for input.", file=sys.stderr)
        return

    if jsonl:
        for ev in waiting:
            print(json.dumps(ev, separators=(",", ":")))
        return

    # Human-readable table
    print(f"{'AGO':>10}  {'PROJECT':<40}  {'REASON':<40}  SESSION_ID")
    print(f"{'---':>10}  {'-------':<40}  {'------':<40}  ----------")
    for ev in waiting:
        ago = time_ago(ev.get("_ts", ""))
        proj = ev.get("project", "?")
        reason = ev.get("reason", "?")
        sid = ev.get("session_id", "?")
        # Truncate long fields
        if len(proj) > 40:
            proj = "..." + proj[-37:]
        if len(reason) > 40:
            reason = reason[:37] + "..."
        print(f"{ago:>10}  {proj:<40}  {reason:<40}  {sid[:12]}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query observatory logs by hook event type.",
        epilog=(
            "Examples:\n"
            "  %(prog)s PreToolUse                        # human-readable output\n"
            "  %(prog)s PreToolUse --jsonl | jq '.'        # JSONL for piping\n"
            "  %(prog)s PreToolUse --tool Bash --jsonl      # Bash events as JSONL\n"
            "  %(prog)s --waiting                           # sessions needing input now\n"
            "  %(prog)s --waiting=all --jsonl | jq '.reason' # all wait history\n"
            "  %(prog)s --jsonl | jq -r '._event' | sort -u  # list event types\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "events",
        nargs="*",
        metavar="EVENT",
        help="Hook event types to include (e.g. PreToolUse PostToolUse Stop). "
        "If omitted, all events pass through.",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Output compact JSONL (one JSON object per line). "
        "Default is indented JSON for readability.",
    )
    parser.add_argument(
        "--tool",
        metavar="NAME",
        help="Filter by tool_name (e.g. Bash, Read, Write, Edit).",
    )
    parser.add_argument(
        "--session",
        metavar="ID",
        help="Filter by session_id (prefix match).",
    )
    parser.add_argument(
        "-f",
        "--file",
        action="append",
        metavar="PATH",
        help="Log file(s) to read. Can be specified multiple times. "
        f"Default: all *.log files in {DEFAULT_LOG_DIR}/",
    )
    parser.add_argument(
        "-n",
        "--last",
        type=int,
        metavar="N",
        help="Show only the last N matching events.",
    )
    parser.add_argument(
        "--waiting",
        nargs="?",
        const="recent",
        default=None,
        metavar="MODE",
        help="Show sessions waiting for user input. "
        "Modes: 'recent' (default) = currently waiting sessions only; "
        "'all' = every waiting event in history. "
        "Detects: PermissionRequest, idle_prompt, permission_prompt, "
        "elicitation_dialog.",
    )
    return parser.parse_args()


def discover_log_files() -> list[Path]:
    """Find all .log files in the default log directory."""
    if not DEFAULT_LOG_DIR.is_dir():
        return []
    return sorted(DEFAULT_LOG_DIR.glob("*.log"))


def iter_lines(sources: list[Path] | None) -> Iterator[str]:
    """Yield lines from files or stdin, one at a time (streaming)."""
    if sources:
        for path in sources:
            try:
                with open(path) as f:
                    yield from f
            except FileNotFoundError:
                print(f"Warning: {path} not found, skipping.", file=sys.stderr)
    else:
        yield from sys.stdin


def matches(event: dict, args: argparse.Namespace) -> bool:
    """Check if an event matches all active filters."""
    if args.events and event.get("_event") not in args.events:
        return False
    if args.tool and event.get("tool_name") != args.tool:
        return False
    if args.session:
        sid = event.get("session_id", "")
        if not sid.startswith(args.session):
            return False
    return True


def format_event(event: dict, jsonl: bool) -> str:
    """Format an event for output."""
    if jsonl:
        return json.dumps(event, separators=(",", ":"))
    return json.dumps(event, indent=2)


def resolve_sources(args: argparse.Namespace) -> list[Path] | None:
    """Determine input sources: explicit --file > auto-discovered logs > stdin."""
    if args.file:
        return [Path(f) for f in args.file]
    discovered = discover_log_files()
    if discovered:
        names = ", ".join(p.name for p in discovered)
        print(f"Reading: {names}", file=sys.stderr)
        return discovered
    if not sys.stdin.isatty():
        return None  # read stdin
    print(
        f"No log files found in {DEFAULT_LOG_DIR}/\n"
        "Start an observatory with run-with-tee-logrotator.sh first,\n"
        "or pipe JSONL into stdin, or specify --file.",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    args = parse_args()
    sources = resolve_sources(args)

    # --waiting mode: separate code path
    if args.waiting is not None:
        run_waiting(args, sources)
        return

    # Standard filter mode: parse, filter, output (streaming)
    # When --last is used, buffer only the tail in a deque.
    use_tail = args.last and args.last > 0
    tail: deque[dict] = deque(maxlen=args.last) if use_tail else deque()

    for line in iter_lines(sources):
        line = line.strip()
        if not line or line[0] != "{":
            continue  # fast skip non-JSON lines (YAML separators, etc.)
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not matches(event, args):
            continue
        if use_tail:
            tail.append(event)
        else:
            print(format_event(event, args.jsonl))

    # Flush tail buffer
    if use_tail:
        for event in tail:
            print(format_event(event, args.jsonl))


if __name__ == "__main__":
    main()
