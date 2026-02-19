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

    # Custom columns with tmux info
    ./scripts/query-hooks.py --waiting --columns state,ago,tmux_target,project,session_id

    # CSV export
    ./scripts/query-hooks.py --waiting --csv --columns state,session_id,project,tmux_target

    # Live dashboard: refresh every 2 seconds (default)
    ./scripts/query-hooks.py --waiting --watch

    # Custom refresh interval
    ./scripts/query-hooks.py --waiting --watch=1.5

    # Live filter view
    ./scripts/query-hooks.py PreToolUse --watch=3
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_T0 = time.monotonic()
VERSION = "0.7.0"

DEFAULT_LOG_DIR = Path("/tmp/claude/observatory")

# Events we track for state derivation (all meaningful hook events)
TRACKED_EVENTS = {
    "SessionStart",
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

# Known columns for --waiting mode (used by --columns validation)
KNOWN_COLUMNS = {
    "state", "ago", "project", "reason", "session_id", "cwd", "start_cwd",
    "alive", "match", "_ts", "_version",
    "tmux_session", "tmux_window", "tmux_pane", "tmux_cwd", "tmux_target",
}

# Default table columns (unchanged from pre-0.5.0 behavior)
DEFAULT_TABLE_COLUMNS = ["tmux_target", "state", "ago", "project", "reason", "session_id"]

# Column descriptions for --columns-help
COLUMN_DESCRIPTIONS: dict[str, str] = {
    "state":        "Session state: FRESH, PERMIT, QUESTION, IDLE, RUN:*, DEAD. "
                    "Derived from last tracked hook event + /proc liveness check.",
    "ago":          "Human-readable time since last event (e.g. '5m ago'). "
                    "Virtual column computed from _ts.",
    "project":      "Short project name derived from git root (preferred) or path. "
                    "Walks upward from start_cwd (or cwd) looking for .git, then takes "
                    "last 2 components (e.g. 'edu-samples/claude-code-hooks-observatory'). "
                    "Falls back to last 2 components of the raw path if no git root found.",
    "reason":       "Compact detail beyond what state shows. "
                    "E.g. tool name for PERMIT, message for QUESTION, empty when "
                    "state is self-explanatory (IDLE, FRESH, RUN:Tool).",
    "session_id":   "Claude Code session UUID. Unique per session, persists across "
                    "resumes. Truncated to 12 chars in table mode.",
    "cwd":          "Latest working directory from the most recent hook event. "
                    "May drift into subdirectories as Claude navigates.",
    "start_cwd":    "Working directory from SessionStart event — the directory where "
                    "'claude' was launched. Stable; best for /proc CWD matching.",
    "alive":        "Boolean (true/false). True for all non-DEAD states. "
                    "Backwards-compatible with pre-0.3.0 output.",
    "match":        "Liveness detection method that matched this session: "
                    "exact:start, exact:last, ancestor:start, ancestor:last, or '' (dead).",
    "_ts":          "ISO 8601 timestamp of the last tracked hook event "
                    "(e.g. '2026-02-17T19:09:44+00:00').",
    "_version":     "query-hooks.py version that produced this record.",
    "tmux_session": "Tmux session name (e.g. 'main'). Obtained via "
                    "'tmux list-panes -a'. Omitted if session is not in tmux.",
    "tmux_window":  "Tmux window index within the session (e.g. '2').",
    "tmux_pane":    "Tmux pane index within the window (e.g. '0').",
    "tmux_cwd":     "Tmux pane's current working directory (from #{pane_current_path}). "
                    "May differ from cwd if Claude navigated elsewhere.",
    "tmux_target":  "Tmux target spec in session:window.pane format (e.g. 'main:2.0'). "
                    "Non-default servers shown as 'main:2.0 [servername]'. "
                    "Usable with 'tmux [-L servername] select-pane -t main:2.0'.",
}


@dataclass
class TmuxInfo:
    """Tmux pane metadata for a process."""
    session_name: str
    window_index: str
    pane_index: str
    pane_cwd: str
    server_name: str = ""  # empty = default server

    @property
    def target(self) -> str:
        """Tmux target spec, e.g. 'main:2.0' or 'main:2.0 [ubertmux]'."""
        base = f"{self.session_name}:{self.window_index}.{self.pane_index}"
        if self.server_name and self.server_name != "default":
            return f"{base} [{self.server_name}]"
        return base


def _liveness_check(rec: dict, live_cwds: set[str]) -> str:
    """Check if a session's process is still running.

    Returns the match method used, or empty string if not alive:
      "exact:start" — exact match on SessionStart CWD
      "exact:last"  — exact match on latest hook CWD
      "ancestor:start" — start CWD is under a live process CWD
      "ancestor:last"  — last CWD is under a live process CWD
      ""            — no match (dead)
    """
    start_cwd = rec.get("start_cwd", "")
    last_cwd = rec.get("cwd", "")

    # Layer 1: exact match on start CWD (most reliable)
    if start_cwd and start_cwd in live_cwds:
        return "exact:start"
    # Layer 2: exact match on latest CWD
    if last_cwd and last_cwd in live_cwds:
        return "exact:last"
    # Layer 3: path ancestry — hook CWD is a subdirectory of a live process CWD
    for proc_cwd in live_cwds:
        if start_cwd:
            try:
                Path(start_cwd).relative_to(proc_cwd)
                return "ancestor:start"
            except ValueError:
                pass
        if last_cwd and last_cwd != start_cwd:
            try:
                Path(last_cwd).relative_to(proc_cwd)
                return "ancestor:last"
            except ValueError:
                pass
    return ""


def session_state(rec: dict, live_cwds: set[str]) -> tuple[str, str]:
    """Derive display state from a session's last event.

    Returns (state, liveness_method) where liveness_method is how the
    process was matched (e.g. "exact:start", "ancestor:last") or "" if dead.

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
        return "TERMINATED", ""

    ev = rec["last_event"]
    match_method = _liveness_check(rec, live_cwds)

    if ev is None:
        return ("DEAD", "") if not match_method else ("RUN:?", match_method)

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
    elif etype == "SessionStart":
        state = "RUN:start"
    else:
        state = "RUN:?"

    # Override: any state + dead process → DEAD
    if not match_method:
        return "DEAD", ""

    return state, match_method


def state_reason(ev: dict | None, state: str) -> str:
    """Compact reason adding info beyond what state already shows.

    Avoids repeating what's visible in the state column (e.g. RUN:Bash
    already tells you the tool, so reason is empty).
    """
    if ev is None:
        return ""

    etype = ev.get("_event", "")
    if etype == "Stop":
        return ""  # FRESH is self-explanatory
    if etype == "PermissionRequest":
        return ev.get("tool_name", "?")
    if etype == "Notification":
        ntype = ev.get("notification_type", "")
        msg = ev.get("message", "")
        match ntype:
            case "idle_prompt":
                return ""  # IDLE is self-explanatory
            case "permission_prompt":
                # Extract tool name from "Claude needs your permission to use X"
                if msg.startswith("Claude needs your permission to use "):
                    return msg.removeprefix("Claude needs your permission to use ")
                return msg or ""
            case "elicitation_dialog":
                return msg or ""
            case _:
                return msg or ntype
    if etype == "PreToolUse":
        return ""  # RUN:Tool already shows tool name
    if etype == "UserPromptSubmit":
        return ""  # RUN:think is self-explanatory
    if etype == "SubagentStart":
        return ""  # RUN:agent is self-explanatory
    if etype in ("PostToolUse", "PostToolUseFailure"):
        return f"after {ev.get('tool_name', '?')}"
    if etype == "SubagentStop":
        return "after agent"
    if etype == "SessionStart":
        return ""  # RUN:start is self-explanatory
    return ""


_git_root_cache: dict[str, str | None] = {}


def _find_git_root(path: str) -> str | None:
    """Walk upward from path looking for a .git directory (or file, for worktrees).

    Returns the directory containing .git, or None if not found.
    Caches results (including intermediate paths) to avoid repeated filesystem walks.
    """
    if path in _git_root_cache:
        return _git_root_cache[path]

    walked: list[str] = []
    current = Path(path)

    for _ in range(20):
        s = str(current)
        if s in _git_root_cache:
            result = _git_root_cache[s]
            for p in walked:
                _git_root_cache[p] = result
            _git_root_cache[path] = result
            return result

        walked.append(s)
        if (current / ".git").exists():
            result = s
            for p in walked:
                _git_root_cache[p] = result
            _git_root_cache[path] = result
            return result

        parent = current.parent
        if parent == current:
            break
        current = parent

    # No git root found — cache negative result for all walked paths
    for p in walked:
        _git_root_cache[p] = None
    _git_root_cache[path] = None
    return None


_remote_name_cache: dict[str, str | None] = {}


def _parse_org_repo(url: str) -> str | None:
    """Extract org/repo from a git remote URL.

    Handles:
      git@github.com:org/repo.git
      https://github.com/org/repo.git
      ssh://git@github.com/org/repo.git
    """
    # SSH shorthand: git@host:org/repo.git
    m = re.match(r"[^@]+@[^:]+:(.+/.+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    # HTTPS or SSH URL: ...host/org/repo.git
    m = re.match(r"(?:https?|ssh)://[^/]+/(.+/.+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    return None


def _project_name_from_remote(git_root: str) -> str | None:
    """Read .git/config and extract org/repo from remotes.

    Tries 'origin' first, then any other remote. Returns None on failure.
    Caches results keyed by git_root.
    """
    if git_root in _remote_name_cache:
        return _remote_name_cache[git_root]

    config_path = Path(git_root) / ".git" / "config"
    try:
        text = config_path.read_text()
    except OSError:
        _remote_name_cache[git_root] = None
        return None

    # Parse remote sections: [remote "name"] ... url = ...
    remotes: dict[str, str] = {}
    current_remote: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        m = re.match(r'\[remote "(.+)"\]', stripped)
        if m:
            current_remote = m.group(1)
            continue
        if stripped.startswith("["):
            current_remote = None
            continue
        if current_remote and stripped.startswith("url = "):
            remotes[current_remote] = stripped[6:].strip()

    # Try origin first, then other remotes
    for name in ["origin"] + [n for n in remotes if n != "origin"]:
        if name in remotes:
            result = _parse_org_repo(remotes[name])
            if result:
                _remote_name_cache[git_root] = result
                return result

    _remote_name_cache[git_root] = None
    return None


def project_name(path: str) -> str:
    """Extract a short project name from git remote, git root, or raw path.

    Fallback chain:
      1. Git remote origin — parse org/repo from .git/config URL
      2. Other git remotes — try non-origin remotes
      3. Git root — last 2 path components of the git root directory
      4. Last 2 path components of the input path
      5. "?" if no path available
    """
    if not path:
        return "?"
    git_root = _find_git_root(path)
    if git_root:
        remote_name = _project_name_from_remote(git_root)
        if remote_name:
            return remote_name
    base = git_root or path
    parts = Path(base).parts
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


def get_claude_pid_cwd_map() -> dict[int, str]:
    """Get {pid: cwd} for all running claude processes via /proc.

    On Linux, reads /proc/<pid>/cwd symlinks for processes named 'claude'.
    Falls back to empty dict on non-Linux or permission errors.
    """
    pid_map: dict[int, str] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return pid_map
    for pid_dir in proc.iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            comm = (pid_dir / "comm").read_text().strip()
            if comm != "claude":
                continue
            cwd = os.readlink(pid_dir / "cwd")
            pid_map[int(pid_dir.name)] = cwd
        except (OSError, PermissionError):
            continue
    return pid_map


def get_live_claude_cwds() -> set[str]:
    """Get working directories of all running claude processes via /proc.

    Thin wrapper around get_claude_pid_cwd_map() for backwards compatibility.
    """
    return set(get_claude_pid_cwd_map().values())


def get_proc_ppid(pid: int) -> int | None:
    """Read parent PID from /proc/<pid>/stat. Returns None on failure."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        # Format: pid (comm) state ppid ...
        # comm can contain spaces/parens, so find last ')' first
        close_paren = stat.rfind(")")
        if close_paren < 0:
            return None
        fields = stat[close_paren + 2:].split()
        # fields[0] = state, fields[1] = ppid
        return int(fields[1]) if len(fields) >= 2 else None
    except (OSError, ValueError):
        return None


def _discover_tmux_servers() -> list[str]:
    """Find all tmux server sockets in /tmp/tmux-$UID/.

    Returns server names (socket filenames). Empty list if directory
    doesn't exist or no sockets found.
    """
    tmux_dir = Path(f"/tmp/tmux-{os.getuid()}")
    if not tmux_dir.is_dir():
        return []
    servers = []
    for entry in tmux_dir.iterdir():
        if entry.is_socket():
            servers.append(entry.name)
    return servers


def _query_tmux_server(server_name: str) -> str:
    """Run tmux list-panes on a specific server. Returns stdout or ""."""
    try:
        result = subprocess.run(
            ["tmux", "-L", server_name, "list-panes", "-a", "-F",
             "#{pane_pid} #{pane_current_path} #{session_name} #{window_index} #{pane_index}"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def get_tmux_pane_map() -> dict[int, TmuxInfo]:
    """Parse tmux list-panes across all servers to build {shell_pid: TmuxInfo}.

    Discovers all tmux servers in /tmp/tmux-$UID/ and queries each.
    Non-default servers are tagged in TmuxInfo.server_name so that
    tmux_target shows e.g. 'main:2.0 [ubertmux]'.
    Returns empty dict if tmux is not installed or not running.
    """
    pane_map: dict[int, TmuxInfo] = {}
    servers = _discover_tmux_servers()
    if not servers:
        # Fallback: try default server without discovery
        servers = ["default"]

    for server_name in servers:
        stdout = _query_tmux_server(server_name)
        if not stdout:
            continue
        for line in stdout.splitlines():
            parts = line.split(" ", 4)
            if len(parts) < 5:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            # First server to claim a PID wins (shouldn't conflict)
            if pid not in pane_map:
                pane_map[pid] = TmuxInfo(
                    session_name=parts[2],
                    window_index=parts[3],
                    pane_index=parts[4],
                    pane_cwd=parts[1],
                    server_name=server_name,
                )
    return pane_map


def build_tmux_for_claude(
    claude_pids: dict[int, str], pane_map: dict[int, TmuxInfo],
) -> dict[int, TmuxInfo]:
    """Walk ancestor chain from each claude PID to find its tmux pane.

    Returns {claude_pid: TmuxInfo} for PIDs that are inside a tmux pane.
    Max 15 hops to prevent infinite loops.
    """
    if not pane_map:
        return {}
    result: dict[int, TmuxInfo] = {}
    for claude_pid in claude_pids:
        pid = claude_pid
        for _ in range(15):
            if pid in pane_map:
                result[claude_pid] = pane_map[pid]
                break
            ppid = get_proc_ppid(pid)
            if ppid is None or ppid <= 1:
                break
            pid = ppid
    return result


def match_session_to_claude_pid(
    rec: dict, pid_cwd_map: dict[int, str],
) -> int | None:
    """CWD-match a session record to a claude PID.

    Uses the same 4-layer matching logic as _liveness_check but returns
    the matched PID instead of the method string.
    """
    start_cwd = rec.get("start_cwd", "")
    last_cwd = rec.get("cwd", "")

    # Layer 1: exact match on start CWD
    if start_cwd:
        for pid, cwd in pid_cwd_map.items():
            if cwd == start_cwd:
                return pid
    # Layer 2: exact match on latest CWD
    if last_cwd:
        for pid, cwd in pid_cwd_map.items():
            if cwd == last_cwd:
                return pid
    # Layer 3: path ancestry on start CWD
    if start_cwd:
        for pid, cwd in pid_cwd_map.items():
            try:
                Path(start_cwd).relative_to(cwd)
                return pid
            except ValueError:
                pass
    # Layer 4: path ancestry on last CWD
    if last_cwd and last_cwd != start_cwd:
        for pid, cwd in pid_cwd_map.items():
            try:
                Path(last_cwd).relative_to(cwd)
                return pid
            except ValueError:
                pass
    return None


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
                "start_cwd": event.get("cwd", ""),  # launch CWD (for /proc matching)
                "cwd": event.get("cwd", ""),         # latest CWD (for display)
            }

        rec = sessions[sid]
        # Always update display cwd to latest
        if event.get("cwd"):
            rec["cwd"] = event["cwd"]
        # Capture start_cwd from SessionStart (most reliable for /proc match)
        if etype == "SessionStart" and event.get("cwd"):
            rec["start_cwd"] = event["cwd"]

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

    columns = getattr(args, "_columns", None)
    csv_mode = getattr(args, "csv", False)

    if mode == "all":
        _output_all_waiting(all_waiting, args.jsonl)
    else:
        _output_sessions(
            sessions, args.jsonl, without_dead, sources, args.no_stats,
            columns=columns, csv_mode=csv_mode,
        )


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


def parse_columns(spec: str) -> list[str]:
    """Validate comma-separated column spec against known set.

    Exits with an error if any column is unknown.
    """
    cols = [c.strip() for c in spec.split(",") if c.strip()]
    unknown = [c for c in cols if c not in KNOWN_COLUMNS]
    if unknown:
        print(
            f"Error: unknown column(s): {', '.join(unknown)}\n"
            f"Available: {', '.join(sorted(KNOWN_COLUMNS))}",
            file=sys.stderr,
        )
        sys.exit(1)
    return cols


def record_get_col(record: dict, col: str) -> str:
    """Resolve a column value from a record, including virtual columns."""
    if col == "ago":
        return time_ago(record.get("_ts", ""))
    val = record.get(col, "")
    if isinstance(val, bool):
        return str(val).lower()
    return str(val) if val is not None else ""


# Column display widths for table mode (min width, max/truncate width)
_COL_WIDTHS: dict[str, tuple[int, int]] = {
    "state": (8, 20),
    "ago": (8, 12),
    "project": (10, 35),
    "reason": (6, 20),
    "session_id": (12, 12),
    "cwd": (10, 60),
    "start_cwd": (10, 60),
    "tmux_session": (8, 20),
    "tmux_window": (6, 6),
    "tmux_pane": (5, 5),
    "tmux_cwd": (10, 60),
    "tmux_target": (10, 20),
}


def _render_table(records: list[dict], columns: list[str]) -> None:
    """Render records as a formatted table to stdout."""
    # Compute column widths: max of header and data, clamped to configured limits
    col_widths: dict[str, int] = {}
    for col in columns:
        min_w, max_w = _COL_WIDTHS.get(col, (8, 30))
        data_w = max((len(record_get_col(r, col)) for r in records), default=0)
        header_w = len(col.upper())
        col_widths[col] = max(min(max(data_w, header_w), max_w), min_w)

    # Header
    hdr_parts = []
    sep_parts = []
    for col in columns:
        w = col_widths[col]
        if col == "ago":
            hdr_parts.append(f"{col.upper():>{w}}")
            sep_parts.append(f"{'---':>{w}}")
        else:
            hdr_parts.append(f"{col.upper():<{w}}")
            sep_parts.append(f"{'-' * min(len(col), w):<{w}}")
    print("  ".join(hdr_parts))
    print("  ".join(sep_parts))

    # Rows
    for r in records:
        row_parts = []
        for col in columns:
            w = col_widths[col]
            val = record_get_col(r, col)
            # Truncate
            if len(val) > w:
                if col in ("cwd", "start_cwd", "tmux_cwd", "project"):
                    val = "..." + val[-(w - 3):]
                else:
                    val = val[:w - 3] + "..."
            if col == "ago":
                row_parts.append(f"{val:>{w}}")
            elif col == "session_id":
                row_parts.append(f"{val[:w]:<{w}}")
            else:
                row_parts.append(f"{val:<{w}}")
        print("  ".join(row_parts))


def _output_csv(records: list[dict], columns: list[str]) -> None:
    """Output records as CSV to stdout."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for r in records:
        row = {col: record_get_col(r, col) for col in columns}
        writer.writerow(row)
    sys.stdout.write(buf.getvalue())


def _output_sessions(
    sessions: dict[str, dict], jsonl: bool, without_dead: bool,
    sources: list[Path] | None, no_stats: bool,
    columns: list[str] | None = None, csv_mode: bool = False,
) -> None:
    """Output sessions with rich state, grouped by state category."""
    # Get PID→CWD map (used for both liveness and tmux correlation)
    pid_cwd_map = get_claude_pid_cwd_map()
    live_cwds = set(pid_cwd_map.values())

    # Tmux correlation (graceful: empty if tmux unavailable)
    pane_map = get_tmux_pane_map()
    tmux_for_claude = build_tmux_for_claude(pid_cwd_map, pane_map)

    # Build output records with derived state.
    # 1-to-1 PID matching: sort sessions by recency so the most recent session
    # in each CWD claims the running PID; older sessions become DEAD.
    sorted_sids = sorted(
        sessions.keys(),
        key=lambda s: _ts_sortval(
            (sessions[s]["last_event"] or {}).get("_ts", "")
        ),
        reverse=True,
    )
    claimed_pids: set[int] = set()
    records: list[dict] = []
    for sid in sorted_sids:
        rec = sessions[sid]
        # Skip terminated sessions entirely
        if rec["terminated"]:
            continue

        # Try to claim a PID for this session (1-to-1).
        # Build a reduced pid_cwd_map excluding already-claimed PIDs.
        avail_pid_cwd = {
            pid: cwd for pid, cwd in pid_cwd_map.items()
            if pid not in claimed_pids
        }
        matched_pid = match_session_to_claude_pid(rec, avail_pid_cwd)

        # Derive state using only unclaimed live CWDs
        unclaimed_cwds = set(avail_pid_cwd.values())
        state, match_method = session_state(rec, unclaimed_cwds)

        if matched_pid and state != "DEAD":
            claimed_pids.add(matched_pid)

        # Skip dead if --without-dead
        if without_dead and state == "DEAD":
            continue

        ev = rec["last_event"]
        ts = ev.get("_ts", "") if ev else ""
        cwd = rec["cwd"]
        reason = state_reason(ev, state)
        alive = state != "DEAD"

        record: dict = {
            "_ts": ts,
            "session_id": sid,
            "state": state,
            "alive": alive,
            "match": match_method,
            "reason": reason,
            "cwd": cwd,
            "start_cwd": rec.get("start_cwd", ""),
            "project": project_name(rec.get("start_cwd", "") or cwd),
            "_version": VERSION,
        }

        # Tmux correlation
        if matched_pid and matched_pid in tmux_for_claude:
            ti = tmux_for_claude[matched_pid]
            record["tmux_session"] = ti.session_name
            record["tmux_window"] = ti.window_index
            record["tmux_pane"] = ti.pane_index
            record["tmux_cwd"] = ti.pane_cwd
            record["tmux_target"] = ti.target

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

    if csv_mode:
        _output_csv(records, columns or DEFAULT_TABLE_COLUMNS)
        return

    # Single-line summary: counts + source files + timing + version
    counts: dict[str, int] = {}
    for r in records:
        group = r["state"] if not r["state"].startswith("RUN:") else "RUN"
        counts[group] = counts.get(group, 0) + 1
    summary_parts = [f"{v} {k}" for k, v in counts.items()]
    parts = [f"{', '.join(summary_parts)} ({len(records)} total)"]
    if sources:
        names = ", ".join(p.name for p in sources)
        parts.append(f"from {names}")
    if not no_stats:
        elapsed = time.monotonic() - _T0
        parts.append(f"in {elapsed:.3f}s (v{VERSION})")
    print(" | ".join(parts), file=sys.stderr)
    # Liveness match method stats
    match_counts: dict[str, int] = {}
    for r in records:
        m = r.get("match", "") or "dead"
        match_counts[m] = match_counts.get(m, 0) + 1
    match_summary = ", ".join(f"{v} {k}" for k, v in match_counts.items())
    print(f"  liveness: {match_summary}", file=sys.stderr)

    # Table output via column-aware renderer
    _render_table(records, columns or DEFAULT_TABLE_COLUMNS)


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
            "  %(prog)s --waiting --columns state,ago,tmux_target  # custom columns\n"
            "  %(prog)s --waiting --csv --columns state,project   # CSV export\n"
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
    parser.add_argument(
        "--columns",
        metavar="COLS",
        help="Comma-separated list of columns to display. "
        f"Default for --waiting: {','.join(DEFAULT_TABLE_COLUMNS)}. "
        f"Available (--waiting mode): {', '.join(sorted(KNOWN_COLUMNS))}. "
        "In filter mode, selects keys from raw event JSON.",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Output CSV (requires --waiting).",
    )
    parser.add_argument(
        "--columns-help",
        action="store_true",
        help="Show detailed description of each available column and exit.",
    )
    parser.add_argument(
        "--watch",
        nargs="?",
        const=2.0,
        default=None,
        type=float,
        metavar="SECS",
        help="Auto-refresh output every SECS seconds (default: 2.0). "
        "Clears screen between refreshes, like watch(1). Ctrl-C to stop.",
    )
    return parser.parse_args()


def discover_log_files() -> list[Path]:
    """Find all log files in the default log directory.

    Matches both current (*.log) and rotated files (*.log.1, *.log.2, ...).
    Rotated files are sorted numerically (oldest first) so events are
    read in chronological order: *.log.10, *.log.9, ..., *.log.1, *.log
    """
    if not DEFAULT_LOG_DIR.is_dir():
        return []
    current = sorted(DEFAULT_LOG_DIR.glob("*.log"))
    rotated = sorted(
        DEFAULT_LOG_DIR.glob("*.log.[0-9]*"),
        key=lambda p: -int(p.suffix.lstrip(".")),  # .10 before .9 before .1
    )
    return rotated + current  # oldest rotated first, current last


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


def _print_columns_help() -> None:
    """Print detailed column descriptions and exit."""
    # Find max column name length for alignment
    name_w = max(len(name) for name in COLUMN_DESCRIPTIONS)
    default_set = set(DEFAULT_TABLE_COLUMNS)
    print(f"Available columns for --waiting mode (default: {','.join(DEFAULT_TABLE_COLUMNS)}):\n")
    for name in sorted(COLUMN_DESCRIPTIONS):
        desc = COLUMN_DESCRIPTIONS[name]
        marker = " *" if name in default_set else "  "
        print(f" {marker} {name:<{name_w}}  {desc}")
    print(f"\n * = included in default table output")
    print(f"\nIn filter mode (no --waiting), --columns selects keys from raw event JSON")
    print(f"without validation — any key present in the event will be included.")


def _run_once(args: argparse.Namespace) -> None:
    """Execute a single query run (shared by normal and --watch modes)."""
    # Validate --csv requires --waiting
    if args.csv and args.waiting is None:
        print("Error: --csv requires --waiting", file=sys.stderr)
        sys.exit(1)

    # Parse --columns (store as _columns to avoid argparse conflict)
    # In --waiting mode, validate against known columns; in filter mode, accept any keys
    if args.columns:
        if args.waiting is not None:
            args._columns = parse_columns(args.columns)
        else:
            args._columns = [c.strip() for c in args.columns.split(",") if c.strip()]
    else:
        args._columns = None

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
        # --columns in filter mode: select specific keys
        if args._columns:
            event = {k: event[k] for k in args._columns if k in event}
        if use_tail:
            tail.append(event)
        else:
            print(format_event(event, args.jsonl))

    # Flush tail buffer
    if use_tail:
        for event in tail:
            print(format_event(event, args.jsonl))

    if not args.no_stats:
        parts = []
        if sources:
            names = ", ".join(p.name for p in sources)
            parts.append(f"Read {names}")
        elapsed = time.monotonic() - _T0
        parts.append(f"in {elapsed:.3f}s")
        print(" | ".join(parts), file=sys.stderr)


def main() -> None:
    global _T0
    args = parse_args()

    if args.columns_help:
        _print_columns_help()
        return

    if args.watch is None:
        _run_once(args)
        return

    # --watch mode: validate interval and loop
    interval = args.watch
    if interval <= 0:
        print(f"Error: --watch interval must be positive, got {interval}", file=sys.stderr)
        sys.exit(1)

    argv_str = " ".join(sys.argv[1:])
    try:
        while True:
            _T0 = time.monotonic()
            _git_root_cache.clear()
            _remote_name_cache.clear()
            # Buffer all output, then clear+write in one burst (no blink)
            buf_out, buf_err = io.StringIO(), io.StringIO()
            real_out, real_err = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = buf_out, buf_err
            try:
                _run_once(args)
            finally:
                sys.stdout, sys.stderr = real_out, real_err
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            header = f"Every {interval}s: query-hooks.py {argv_str}    {now}\n\n"
            # Atomic swap: clear screen + header + stderr + stdout in one write
            real_err.write(f"\033[2J\033[H{header}{buf_err.getvalue()}")
            real_err.flush()
            out = buf_out.getvalue()
            if out:
                real_out.write(out)
                real_out.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print(file=sys.stderr)  # clean line after ^C


if __name__ == "__main__":
    main()
