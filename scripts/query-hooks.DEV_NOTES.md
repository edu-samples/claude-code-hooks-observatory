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

### CWD mismatch problem

Hook events can report a **different CWD than the process's actual CWD** (`/proc/<pid>/cwd`). Observed scenarios:

#### Scenario 1: Hook CWD drifts from process CWD

A session launched from `/home/user/project` (process CWD stays here) works in a subdirectory, and later hook events report that subdirectory as `cwd`.

```
Process CWD (from /proc):     /home/user/d/26Q1/de
Hook CWD (from last event):   /home/user/d/26Q1/de/Swiss_Official_Documents/naturalization-knowledge-extraction
```

#### Scenario 2: Two sessions, same process CWD

Multiple sessions started from the same directory. If one is dead and the other alive, the alive process's CWD makes **both** appear alive.

#### Scenario 3: Resumed session (`claude -r`)

Session resumed with `claude -r` from a parent directory. The process CWD is the parent, but hook events report the original project subdirectory.

### Layered liveness detection (implemented: Method B + A)

`_liveness_check(rec, live_cwds)` returns the match method used, or `""` if dead. Four layers tried in order (first match wins):

```
Layer   Method          Field        Match type     Covers
─────   ──────          ─────        ──────────     ──────
  1     exact:start     start_cwd    exact          Normal sessions (most common)
  2     exact:last      cwd          exact          Sessions without SessionStart in log
  3     ancestor:start  start_cwd    path ancestry  claude -r from parent dir
  4     ancestor:last   cwd          path ancestry  CWD drift into subdirectory (Scenario 1)
```

**Per-session CWD tracking:**

```python
sessions[sid] = {
    "start_cwd": cwd,    # from SessionStart event, never overwritten (Method B)
    "cwd": cwd,          # latest from any event (for display + fallback matching)
    ...
}
```

**Path ancestry** (Method A): checks if the hook CWD is a subdirectory of any live process CWD using `Path.relative_to()`. Only checks hook-under-process direction (not reverse), since the common case is the hook CWD drifting deeper.

**Match method reporting:** each JSONL record includes a `"match"` field showing which layer matched (e.g. `"exact:start"`, `"ancestor:last"`). The summary line shows aggregate stats for at-a-glance debugging:

```
  liveness: 19 exact:start, 2 ancestor:last, 3 dead
```

### Possible future improvements

#### Method C: Claude project directory lookup

Claude stores sessions in `~/.claude/projects/{encoded-cwd}/{session-uuid}.jsonl`. The `{encoded-cwd}` is derived from the **process launch CWD** (replacing `/` with `-`). Given a session UUID, find which project directory contains it to recover the original launch CWD — even without `SessionStart` in the log.

```
Session 1796e25b → found in ~/.claude/projects/-home-gw-t490-d-26Q1-de/
Project dir name → encodes /home/gw-t490/d/26Q1/de (the process CWD)
/proc/648714/cwd → /home/gw-t490/d/26Q1/de → exact match!
```

| Pro | Con |
|-----|-----|
| Recovers launch CWD without SessionStart | Requires filesystem access to `~/.claude/` |
| Handles `claude -r` resumes | Encoding is lossy (dashes ambiguous) — need to match, not decode |
| | Adds I/O (scanning project dirs) |

#### Method E: tmux session correlation

Correlate tmux session → pane CWD → PID via `tmux list-panes -a -F '#{pane_pid} #{pane_current_path}'`.

| Pro | Con |
|-----|-----|
| Direct PID-to-CWD mapping | Not all users use tmux; not portable |

### Known limitations

* **CWD-based, not session-ID-based.** If two sessions share the same CWD, both show as alive if either process is running (Scenario 2). Best-effort heuristic since Claude Code doesn't expose session IDs via the process table.

* **Ancestry false positives.** Path ancestry matching can match unrelated sessions that happen to share a parent directory. Mitigated by trying exact match first (layers 1-2) and only falling back to ancestry (layers 3-4) when exact fails.

* **Linux-only.** Falls back to empty set on macOS/other platforms, meaning all sessions show as alive (conservative — no false negatives).

* **Race condition.** A process could exit between the log scan and the `/proc` scan. Unlikely in practice since the log scan takes milliseconds.

* **Log rotation boundaries.** Sessions that started before a log rotation may only have `SessionStart` (or nothing) in the current log epoch. The script reads rotated files (`*.log.1`, `*.log.2`, ...) in chronological order to reconstruct session lifecycles, but events lost to server restarts cannot be recovered.

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
