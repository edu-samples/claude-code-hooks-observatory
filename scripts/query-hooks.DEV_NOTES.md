# query-hooks.py — Developer Notes

## Architecture

Single-file, zero-dependency Python script. Two code paths:

```
main()
├── --waiting mode  →  run_waiting()  →  _output_sessions() / _output_all_waiting()
└── filter mode     →  streaming parse + matches() + format_event()
```

### Session state model

Each session's state is derived from its **last tracked event** via `session_state()`. The state determines both display grouping and the human-readable reason.

```
State       Tag         Trigger (last event)                    Meaning
─────       ───         ────────────────────                    ───────
FRESH       FRESH       Stop                                    Just finished turn, waiting (<60s)
PERMIT      PERMIT      PermissionRequest or Notification       Needs user to approve a tool
                         (permission_prompt)
QUESTION    QUESTION    Notification (elicitation_dialog)       Claude is asking the user something
IDLE        IDLE        Notification (idle_prompt)              Waiting for input (60s+ elapsed)
RUNNING     RUN:tool    PreToolUse                              Executing a tool (name shown)
RUNNING     RUN:think   UserPromptSubmit                        AI processing user prompt
RUNNING     RUN:agent   SubagentStart                           Subagent active
RUNNING     RUN:done    PostToolUse/PostToolUseFailure/         Between tools (thinking)
                         SubagentStop
DEAD        DEAD        Any state + no /proc match              Process exited without SessionEnd
```

Sessions with `SessionEnd` as their last event → `TERMINATED` → filtered out entirely.

### Per-session tracking

```python
sessions[sid] = {
    "last_event": event,       # full event dict (or None)
    "last_event_type": etype,  # for quick classification
    "terminated": False,       # SessionEnd seen
    "cwd": cwd,                # latest working directory
}
```

Only events in `TRACKED_EVENTS` update `last_event`. Notification events are further filtered by `WAITING_NOTIFICATION_TYPES` to avoid tracking irrelevant notifications.

### State derivation

`session_state(rec, live_cwds)` derives the state:

1. If `terminated` → `TERMINATED` (filtered out)
2. Match `last_event._event` to determine base state
3. Override: if `cwd not in live_cwds` → `DEAD`

This means a session showing `FRESH` in the log but whose process has died will correctly show as `DEAD`.

### Display ordering

```python
STATE_ORDER = {
    "FRESH": 0, "PERMIT": 1, "QUESTION": 2, "IDLE": 3,
    # RUN:* states → 4
    "DEAD": 5,
}
```

Within each group, sessions are sorted by timestamp descending (newest first). This puts actionable items (FRESH, PERMIT, QUESTION) at the top.

### Tracked events

```python
TRACKED_EVENTS = {
    "Stop", "PermissionRequest", "Notification",
    "PreToolUse", "PostToolUse", "PostToolUseFailure",
    "UserPromptSubmit", "SubagentStart", "SubagentStop",
    "SessionEnd",
}
```

This replaces the previous three-group classification (WAITING/ACTIVE/TERMINAL) with a single set. State is derived from the last event rather than from event categories.

### Why Stop maps to FRESH (not IDLE)

`Stop` fires immediately when Claude finishes an assistant turn. The `idle_prompt` notification fires ~60s later. This gives us two distinct states:

* **FRESH** (Stop, <60s) — session just finished, user should respond soon
* **IDLE** (idle_prompt, 60s+) — session has been waiting a while

Previously both were lumped together as "waiting".

### SubagentStop clears subagent Stop events

`Stop` also fires for subagent turns. The sequence is: `Stop` (subagent) → `SubagentStop` (main session). Since `SubagentStop` maps to `RUN:done`, it correctly overrides the subagent's `Stop` → `FRESH` state.

### Dead session detection (two layers)

**Layer 1: SessionEnd events.**
When a session exits cleanly, Claude Code emits a `SessionEnd` event. The script treats this as `TERMINATED` — the session is filtered out entirely.

**Layer 2: /proc cross-reference (Linux).**
For sessions that crashed, were `kill -9`'d, or whose terminal was closed — no `SessionEnd` event exists. The script reads `/proc/<pid>/comm` to find all processes named `claude`, then reads `/proc/<pid>/cwd` to get their working directories. A session's CWD is compared against this set. Any state + no matching process → `DEAD`.

```python
get_live_claude_cwds()  →  set of CWD paths for running claude processes
```

### CWD mismatch problem (current, exact match only)

The current implementation uses **exact CWD string match** between the hook's `cwd` field and `/proc/<pid>/cwd`. This produces false DEAD results in several scenarios:

#### Scenario 1: Hook CWD drifts from process CWD

Claude Code's hook events can report a **different CWD than the process's actual CWD**. Observed: a session launched from `/home/user/d/26Q1/de` (process CWD stays here) starts working in subdirectory `Swiss_Official_Documents/naturalization-knowledge-extraction`, and later hook events report that subdirectory as `cwd`. Since `run_waiting()` updates `rec["cwd"]` to the latest event's CWD, the final recorded CWD no longer matches `/proc/<pid>/cwd`.

```
Process CWD (from /proc):     /home/user/d/26Q1/de
Hook CWD (from last event):   /home/user/d/26Q1/de/Swiss_Official_Documents/naturalization-knowledge-extraction
Result:                        exact match fails → falsely marked DEAD
```

#### Scenario 2: Two sessions, same process CWD

Multiple sessions started from the same directory. If one is dead and the other alive, the alive process's CWD makes **both** appear alive.

#### Scenario 3: resumed session (`claude -r`)

Session resumed with `claude -r` from a parent directory. The process CWD is the parent, but hook events report the original project subdirectory.

### Possible improvements to liveness detection

Several approaches, each with different tradeoffs. They can be combined (try in order, first match wins).

#### Method A: Path ancestry match

Check if either path is a prefix of the other (ancestor/descendant relationship).

```python
def is_path_related(hook_cwd: str, proc_cwd: str) -> bool:
    """True if one path is an ancestor of the other."""
    h = Path(hook_cwd)
    p = Path(proc_cwd)
    try:
        h.relative_to(p)  # hook is under process CWD
        return True
    except ValueError:
        pass
    try:
        p.relative_to(h)  # process is under hook CWD (unlikely but possible)
        return True
    except ValueError:
        return False
```

| Pro | Con |
|-----|-----|
| Simple, zero dependencies | False positives for unrelated projects sharing a parent |
| Covers the common case (subdirectory drift) | Doesn't help if CWDs are completely unrelated |
| Fast (pure string/path comparison) | |

#### Method B: Session-first CWD (use `SessionStart` event CWD)

Track the CWD from the `SessionStart` event separately and use it for `/proc` matching, since the process CWD corresponds to the **launch** directory, not the drifted directory.

```python
sessions[sid] = {
    "start_cwd": cwd,    # from SessionStart, never overwritten
    "last_cwd": cwd,     # latest from any event (for display)
    ...
}
```

| Pro | Con |
|-----|-----|
| Matches the process CWD semantics exactly | Doesn't help if observatory started after session |
| No false positives from ancestry | Requires SessionStart to be in the log |
| Still uses exact match | |

#### Method C: Claude project directory lookup

Claude stores sessions in `~/.claude/projects/{encoded-cwd}/{session-uuid}.jsonl`. The `{encoded-cwd}` is derived from the **process launch CWD** (replacing `/` with `-`). Given a session UUID, find which project directory contains it to recover the original launch CWD.

```
Session 1796e25b → found in ~/.claude/projects/-home-gw-t490-d-26Q1-de/
Project dir name → encodes /home/gw-t490/d/26Q1/de (the process CWD)
/proc/648714/cwd → /home/gw-t490/d/26Q1/de → exact match!
```

| Pro | Con |
|-----|-----|
| Recovers the actual launch CWD | Requires filesystem access to `~/.claude/` |
| Works even without SessionStart in logs | Encoding is lossy (dashes ambiguous) — need to match, not decode |
| Handles `claude -r` resumes | Adds I/O (scanning project dirs) |
| No false positives from ancestry | Need to handle `~/.claude/` not existing |

Implementation sketch:

```python
def session_launch_cwds() -> dict[str, str]:
    """Map session UUIDs to their launch CWD via Claude's project dir structure."""
    result = {}
    projects = Path.home() / ".claude" / "projects"
    if not projects.is_dir():
        return result
    for proj_dir in projects.iterdir():
        if not proj_dir.is_dir():
            continue
        for f in proj_dir.glob("*.jsonl"):
            sid = f.stem  # UUID is the filename without .jsonl
            # Encode each live CWD and compare to proj_dir.name
            result[sid] = proj_dir.name  # store encoded name for matching
    return result
```

Then match: encode each `/proc/<pid>/cwd` as `-`-separated path and compare to the project dir name.

#### Method D: Track both start CWD and all process CWDs

Combine methods: use `SessionStart` CWD for `/proc` matching, fall back to path ancestry, fall back to project dir lookup.

```python
# Priority order for liveness check:
1. Exact match on start_cwd (Method B)
2. Exact match on last_cwd (current)
3. Path ancestry on start_cwd (Method A + B)
4. Path ancestry on last_cwd (Method A)
5. Project dir lookup (Method C)
```

#### Method E: tmux session correlation

Some users name tmux sessions after projects. Could correlate tmux session → pane CWD → PID.

```bash
tmux list-panes -a -F '#{pane_pid} #{pane_current_path}'
```

| Pro | Con |
|-----|-----|
| Direct PID-to-CWD mapping | Not all users use tmux |
| Can see pane CWD which may differ from process CWD | Requires `tmux` binary available |
| | tmux-specific, not portable |

### Known limitations of /proc matching

* **CWD-based, not session-ID-based.** If two sessions share the same CWD, both show as alive if either process is running. This is a best-effort heuristic since Claude Code doesn't expose session IDs via the process table.

* **Linux-only.** Falls back to empty set on macOS/other platforms, meaning all sessions show as alive (conservative — no false negatives).

* **Race condition.** A process could exit between the log scan and the `/proc` scan. Unlikely in practice since the log scan takes milliseconds.

* **Hook CWD drifts.** Claude Code's hook events can report a CWD different from the process CWD. The script tracks the latest CWD from events, which may be a subdirectory the session navigated into. See "CWD mismatch problem" above.

### JSONL backwards compatibility

JSONL output includes both `"state"` (new) and `"alive"` (boolean, backwards-compatible). The `alive` field is `true` for all non-DEAD states.

### Output format

Table mode groups sessions by state (FRESH → PERMIT → QUESTION → IDLE → RUN:* → DEAD), newest first within each group. The STATE column width adapts to the longest state string (e.g. `RUN:WebFetch`).

### Streaming design

The filter mode is fully streaming — events are parsed, filtered, and printed one at a time without buffering the entire log. The only exception is `--last N`, which uses a bounded `deque(maxlen=N)` to buffer only the tail.

The `--waiting` mode necessarily buffers per-session state (one dict per session), but not the full event stream.

### Event timing (observed)

```
Stop → idle_prompt:      ~60s  (idle notification delay)
Stop → SubagentStop:      ~2s  (subagent cleanup)
Stop → UserPromptSubmit:  0-∞s (depends on user)
Stop → SessionEnd:        0-∞s (user closes session)
```
