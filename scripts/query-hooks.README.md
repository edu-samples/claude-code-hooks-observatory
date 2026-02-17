# query-hooks.py

Query and filter observatory JSONL log files. Two modes: **event filtering** (default) and **session state detection** (`--waiting`).

## Prerequisites

* Python 3.10+
* Observatory logs in `/tmp/claude/observatory/` (created by `run-with-tee-logrotator.sh`)
* Or pipe JSONL from stdin, or specify `--file`

## Usage

### Which sessions need my attention?

```bash
./scripts/query-hooks.py --waiting
```

Output:

```
2 PERMIT, 5 RUN, 6 DEAD (13 total)
STATE                AGO  PROJECT                              REASON / DETAIL                      SESSION_ID
-----                ---  -------                              ---------------                      ----------
FRESH           12s ago  tmp/claudetmp                        waiting for input                    320f7800-76a
PERMIT            2m ago  gwwtests/26Q1-security-onto...       permission needed: Bash              37d77420-e50
QUESTION         30s ago  CLIAI/telegram-webctl                What approach do you prefer?          97afb66f-77f
IDLE          3h 2m ago  ...aftermarket.pl-api-pub-kb         idle — waiting for input             9e12a3cf-765
RUN:Bash          5s ago  ...claude-code-hooks-observatory     running: Bash                        9999826b-bd3
RUN:done         40s ago  ...ode-integrations-analysis-...     finished: Task                       6a26deef-54e
DEAD            23m ago  ...naturalization-knowledge-ext...   idle — waiting for input             1796e25b-939
```

### Session states

| State | Meaning |
|-------|---------|
| `FRESH` | Just finished a turn, waiting for input (<60s) |
| `PERMIT` | Needs user to approve a tool |
| `QUESTION` | Claude is asking the user something |
| `IDLE` | Waiting for input (60s+ elapsed) |
| `RUN:tool` | Executing a tool (tool name shown) |
| `RUN:think` | Processing user prompt |
| `RUN:agent` | Subagent active |
| `RUN:done` | Between tools (thinking) |
| `DEAD` | Process exited without clean shutdown |

Sessions with `SessionEnd` are filtered out entirely (terminated cleanly).

### Exclude dead sessions

```bash
./scripts/query-hooks.py --waiting --without-dead
```

### Filter by state via JSONL

```bash
# Only running sessions
./scripts/query-hooks.py --waiting --jsonl | jq 'select(.state | startswith("RUN"))'

# Only sessions needing attention (FRESH, PERMIT, QUESTION, IDLE)
./scripts/query-hooks.py --waiting --jsonl | jq 'select(.state | test("FRESH|PERMIT|QUESTION|IDLE"))'

# Backwards-compatible alive filter
./scripts/query-hooks.py --waiting --jsonl | jq 'select(.alive)'
```

### Live monitoring with watch

```bash
watch -n 2 ./scripts/query-hooks.py --waiting
```

### Custom columns (with tmux info)

```bash
# Show tmux target alongside state
./scripts/query-hooks.py --waiting --columns state,ago,tmux_target,project,session_id

# All tmux details
./scripts/query-hooks.py --waiting --columns state,ago,tmux_session,tmux_window,tmux_pane,tmux_cwd
```

### CSV export

```bash
./scripts/query-hooks.py --waiting --csv --columns state,session_id,project,tmux_target
```

### Filter by event type

```bash
# Human-readable
./scripts/query-hooks.py PreToolUse

# Multiple event types
./scripts/query-hooks.py PreToolUse PostToolUse

# Last 5 events
./scripts/query-hooks.py PreToolUse -n 5
```

### Filter by tool name

```bash
./scripts/query-hooks.py PreToolUse --tool Bash
./scripts/query-hooks.py PreToolUse --tool Write --jsonl
```

### Filter by session

```bash
# Prefix match on session ID
./scripts/query-hooks.py --session 6a26deef
```

### JSONL output for piping

```bash
# Compact JSONL (one JSON object per line)
./scripts/query-hooks.py PreToolUse --jsonl | jq '.tool_input.command // empty'

# Count events by type
./scripts/query-hooks.py --jsonl | jq -r '._event' | sort | uniq -c | sort -rn

# Extract all Bash commands
./scripts/query-hooks.py PreToolUse --tool Bash --jsonl | jq -r '.tool_input.command'

# Full waiting history
./scripts/query-hooks.py --waiting=all --jsonl | jq -r '.reason' | sort | uniq -c | sort -rn
```

### Explicit log files

```bash
./scripts/query-hooks.py PreToolUse -f /path/to/custom.log
./scripts/query-hooks.py PreToolUse -f log1.log -f log2.log
```

## Options

| Flag | Description |
|------|-------------|
| `EVENT [EVENT...]` | Hook event types to include (e.g. `PreToolUse`, `Stop`) |
| `--waiting[=MODE]` | Session state display. `recent` (default): current states. `all`: full wait history |
| `--without-dead` | Exclude dead sessions from `--waiting` output |
| `--no-stats` | Suppress timing stats on stderr |
| `--jsonl` | Compact JSONL output (default: indented JSON) |
| `--tool NAME` | Filter by tool_name (e.g. `Bash`, `Read`, `Write`) |
| `--session ID` | Filter by session_id (prefix match) |
| `-f PATH` | Explicit log file(s). Repeatable. Default: `*.log` in `/tmp/claude/observatory/` |
| `-n N` | Show only the last N matching events |
| `--columns COLS` | Comma-separated column list. In `--waiting`: validated against known set. In filter: selects raw event keys |
| `--csv` | CSV output (requires `--waiting`) |

## JSONL output fields (--waiting)

| Field | Description |
|-------|-------------|
| `state` | Session state: `FRESH`, `PERMIT`, `QUESTION`, `IDLE`, `RUN:*`, `DEAD` |
| `alive` | Boolean, `true` if process is running (backwards-compatible) |
| `reason` | Human-readable description of current state |
| `project` | Short project name from CWD |
| `session_id` | Full session ID |
| `_ts` | Timestamp of last tracked event |
| `cwd` | Full working directory path |
| `start_cwd` | CWD from SessionStart event |
| `match` | Liveness match method (e.g. `exact:start`, `ancestor:last`) |
| `tmux_session` | Tmux session name (omitted if not in tmux) |
| `tmux_window` | Tmux window index |
| `tmux_pane` | Tmux pane index |
| `tmux_cwd` | Tmux pane's current working directory |
| `tmux_target` | Tmux target spec (e.g. `main:2.0`) |

## Input sources (priority order)

1. `--file` paths (explicit)
2. Auto-discovered `*.log` files in `/tmp/claude/observatory/`
3. stdin (if piped)

## Known limitation: false DEAD sessions

Liveness detection uses 4-layer CWD matching (exact + path ancestry on both start and latest CWDs). False DEAD results can still occur when:

* **Two sessions, same CWD** — if one is dead and the other alive, both appear alive (false positive for the dead one)
* **Deeply nested CWD drift** — rare edge cases where neither exact nor ancestry matching connects the hook CWD to the process CWD

See `query-hooks.DEV_NOTES.md` for full details on the liveness detection layers.

## Output streams

* **stdout** — data (JSON/JSONL)
* **stderr** — human messages (file list, summary counts, warnings)
