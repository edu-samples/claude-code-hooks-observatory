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

    # Session states: FRESH, PERMIT, QUESTION, IDLE, RUN:*, DEAD
    ./scripts/query-hooks.py --waiting

    # Exclude dead sessions
    ./scripts/query-hooks.py --waiting --without-dead

    # Filter running sessions via JSONL
    ./scripts/query-hooks.py --waiting --jsonl | jq 'select(.state | startswith("RUN"))'

    # All historical waiting events as JSONL
    ./scripts/query-hooks.py --waiting=all --jsonl | jq '.reason'

    # Count events by type
    ./scripts/query-hooks.py --jsonl | jq -r '._event' | sort | uniq -c | sort -rn
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_T0 = time.monotonic()

DEFAULT_LOG_DIR = Path("/tmp/claude/observatory")

# Events we track for state derivation (all meaningful hook events)
TRACKED_EVENTS = {
    "Stop", "PermissionRequest", "Notification",
    "PreToolUse", "PostToolUse", "PostToolUseFailure",
    "UserPromptSubmit", "SubagentStart", "SubagentStop",
    "SessionEnd",
}

# Notification types relevant to session state
WAITING_NOTIFICATION_TYPES = {"permission_prompt", "idle_prompt", "elicitation_dialog"}

# Display order for state groups (lower = shown first)
STATE_ORDER = {
    "FRESH": 0, "PERMIT": 1, "QUESTION": 2, "IDLE": 3,
    # RUN:* states all get order 4 (see _state_sort_key)
    "DEAD": 5,
}


def session_state(rec: dict, live_cwds: set[str]) -> str:
    """Derive display state from a session's last event.

    States (in display order):
      FRESH    — just finished a turn, waiting for input (<60s)
      PERMIT   — needs user to approve a tool
      QUESTION — Claude is asking the user something
      IDLE     — waiting for input (60s+ elapsed)
      RUN:tool — executing a tool (tool name shown)
      RUN:think — processing user prompt
      RUN:agent — subagent active
      RUN:done  — between tools (thinking)
      DEAD     — process exited without SessionEnd
    """
    if rec["terminated"]:
        return "TERMINATED"

    ev = rec["last_event"]
    if ev is None:
        return "DEAD" if rec["cwd"] not in live_cwds else "RUN:?"

    etype = ev.get("_event", "")

    # Waiting states
    if etype == "Stop":
        state = "FRESH"
    elif etype == "PermissionRequest":
        state = "PERMIT"
    elif etype == "Notification":
        ntype = ev.get("notification_type", "")
        if ntype == "idle_prompt":
            state = "IDLE"
        elif ntype == "permission_prompt":
            state = "PERMIT"
        elif ntype == "elicitation_dialog":
            state = "QUESTION"
        else:
            state = "IDLE"  # unknown notification → treat as idle
    # Running states
    elif etype == "PreToolUse":
        tool = ev.get("tool_name", "?")
        state = f"RUN:{tool}"
    elif etype == "UserPromptSubmit":
        state = "RUN:think"
    elif etype == "SubagentStart":
        state = "RUN:agent"
    elif etype in ("PostToolUse", "PostToolUseFailure", "SubagentStop"):
        state = "RUN:done"
    else:
        state = "RUN:?"

    # Override: any state + dead process → DEAD
    if rec["cwd"] not in live_cwds:
        return "DEAD"

    return state


def state_reason(ev: dict | None, state: str) -> str:
    """Human-readable reason/detail for the session's current state."""
    if ev is None:
        return "no events recorded"

    etype = ev.get("_event", "")
    if etype == "Stop":
        return "waiting for input"
    if etype == "PermissionRequest":
        tool = ev.get("tool_name", "?")
        return f"permission needed: {tool}"
    if etype == "Notification":
        ntype = ev.get("notification_type", "")
        msg = ev.get("message", "")
        match ntype:
            case "idle_prompt":
                return "idle — waiting for input"
            case "permission_prompt":
                return msg or "permission dialog"
            case "elicitation_dialog":
                return msg or "question for user"
            case _:
                return msg or ntype
    if etype == "PreToolUse":
        tool = ev.get("tool_name", "?")
        return f"running: {tool}"
    if etype == "UserPromptSubmit":
        return "processing prompt"
    if etype == "SubagentStart":
        return "subagent active"
    if etype in ("PostToolUse", "PostToolUseFailure"):
        tool = ev.get("tool_name", "?")
        return f"finished: {tool}"
    if etype == "SubagentStop":
        return "subagent finished"
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


def get_live_claude_cwds() -> set[str]:
    """Get working directories of all running claude processes via /proc.

    On Linux, reads /proc/<pid>/cwd symlinks for processes named 'claude'.
    Falls back to empty set on non-Linux or permission errors.
    """
    cwds: set[str] = set()
    proc = Path("/proc")
    if not proc.is_dir():
        return cwds
    for pid_dir in proc.iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            comm = (pid_dir / "comm").read_text().strip()
            if comm != "claude":
                continue
            cwd = os.readlink(pid_dir / "cwd")
            cwds.add(cwd)
        except (OSError, PermissionError):
            continue
    return cwds


def _is_tracked_event(event: dict) -> bool:
    """Is this an event we track for session state?

    For Notification events, only track relevant notification types.
    """
    etype = event.get("_event", "")
    if etype not in TRACKED_EVENTS:
        return False
    if etype == "Notification":
        return event.get("notification_type") in WAITING_NOTIFICATION_TYPES
    return True


def run_waiting(args: argparse.Namespace, sources: list[Path] | None) -> None:
    """Handle --waiting mode: show session states."""
    mode = args.waiting  # "recent" or "all"
    without_dead = getattr(args, "without_dead", False)

    # Per-session tracking
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
                "last_event": None,
                "last_event_type": "",
                "terminated": False,
                "cwd": event.get("cwd", ""),
            }

        rec = sessions[sid]
        # Always update cwd to latest
        if event.get("cwd"):
            rec["cwd"] = event["cwd"]

        if etype == "SessionEnd":
            rec["terminated"] = True
            rec["last_event"] = event
            rec["last_event_type"] = etype
        elif _is_tracked_event(event):
            rec["last_event"] = event
            rec["last_event_type"] = etype

            # For --waiting=all mode, collect waiting events
            if mode == "all" and etype in ("Stop", "PermissionRequest", "Notification"):
                enriched = {
                    "_ts": ts,
                    "_event": etype,
                    "session_id": sid,
                    "reason": state_reason(event, ""),
                    "cwd": event.get("cwd", ""),
                    "project": project_name(event.get("cwd", "")),
                    "tool_name": event.get("tool_name"),
                    "notification_type": event.get("notification_type"),
                    "message": event.get("message"),
                }
                enriched = {k: v for k, v in enriched.items() if v is not None}
                all_waiting.append(enriched)

    if mode == "all":
        _output_all_waiting(all_waiting, args.jsonl)
    else:
        _output_sessions(sessions, args.jsonl, without_dead)


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


def _state_sort_key(state: str) -> int:
    """Sort key for state grouping. RUN:* states all sort together at position 4."""
    if state.startswith("RUN:"):
        return 4
    return STATE_ORDER.get(state, 4)


def _output_sessions(
    sessions: dict[str, dict], jsonl: bool, without_dead: bool
) -> None:
    """Output sessions with rich state, grouped by state category."""
    live_cwds = get_live_claude_cwds()

    # Build output records with derived state
    records: list[dict] = []
    for sid, rec in sessions.items():
        state = session_state(rec, live_cwds)
        # Skip terminated sessions entirely
        if state == "TERMINATED":
            continue
        # Skip dead if --without-dead
        if without_dead and state == "DEAD":
            continue

        ev = rec["last_event"]
        ts = ev.get("_ts", "") if ev else ""
        cwd = rec["cwd"]
        reason = state_reason(ev, state)
        alive = state != "DEAD"

        record = {
            "_ts": ts,
            "session_id": sid,
            "state": state,
            "alive": alive,
            "reason": reason,
            "cwd": cwd,
            "project": project_name(cwd),
        }
        records.append(record)

    if not records:
        print("No active sessions found.", file=sys.stderr)
        return

    # Sort: by state group, then by timestamp descending within each group
    records.sort(key=lambda r: (_state_sort_key(r["state"]), -(
        _ts_sortval(r["_ts"])
    )))

    if jsonl:
        for rec in records:
            print(json.dumps(rec, separators=(",", ":")))
        return

    # Summary counts
    counts: dict[str, int] = {}
    for r in records:
        group = r["state"] if not r["state"].startswith("RUN:") else "RUN"
        counts[group] = counts.get(group, 0) + 1
    summary_parts = [f"{v} {k}" for k, v in counts.items()]
    print(f"{', '.join(summary_parts)} ({len(records)} total)", file=sys.stderr)

    # Table output
    # Determine state column width (RUN:toolname can be long)
    max_state = max(len(r["state"]) for r in records)
    state_w = max(max_state, 8)

    hdr = f"{'STATE':<{state_w}}  {'AGO':>10}  {'PROJECT':<35}  {'REASON / DETAIL':<35}  SESSION_ID"
    sep = f"{'-----':<{state_w}}  {'---':>10}  {'-------':<35}  {'---------------':<35}  ----------"
    print(hdr)
    print(sep)
    for r in records:
        ago = time_ago(r["_ts"])
        proj = r["project"]
        reason = r["reason"]
        sid = r["session_id"]
        state = r["state"]
        # Truncate long fields
        if len(proj) > 35:
            proj = "..." + proj[-32:]
        if len(reason) > 35:
            reason = reason[:32] + "..."
        print(f"{state:<{state_w}}  {ago:>10}  {proj:<35}  {reason:<35}  {sid[:12]}")


def _ts_sortval(ts_str: str) -> float:
    """Convert timestamp to float for sorting. Returns 0.0 on parse failure."""
    try:
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.timestamp()
    except (ValueError, TypeError):
        return 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query observatory logs by hook event type.",
        epilog=(
            "Examples:\n"
            "  %(prog)s PreToolUse                          # human-readable output\n"
            "  %(prog)s PreToolUse --jsonl | jq '.'          # JSONL for piping\n"
            "  %(prog)s PreToolUse --tool Bash --jsonl        # Bash events as JSONL\n"
            "  %(prog)s --waiting                             # all session states\n"
            "  %(prog)s --waiting --without-dead              # exclude dead sessions\n"
            "  %(prog)s --waiting=all --jsonl | jq '.reason'  # all wait history\n"
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
        help="Show session states. "
        "Modes: 'recent' (default) = current session states; "
        "'all' = every waiting event in history. "
        "States: FRESH, PERMIT, QUESTION, IDLE, RUN:*, DEAD.",
    )
    parser.add_argument(
        "--without-dead",
        action="store_true",
        help="Exclude dead sessions from --waiting output.",
    )
    parser.add_argument(
        "--no-stats",
        action="store_true",
        help="Suppress timing stats printed to stderr on exit.",
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
        if not args.no_stats:
            elapsed = time.monotonic() - _T0
            print(f"Completed in {elapsed:.3f}s", file=sys.stderr)
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

    if not args.no_stats:
        elapsed = time.monotonic() - _T0
        print(f"Completed in {elapsed:.3f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
