# Claude Code Hooks Observatory

An educational project for observing Claude Code hook events in real-time. Three implementations teach different IPC and language concepts.

## Why?

Learn how Claude Code hooks work by watching them fire in real-time. Fork this repo to build your own hook logic. Three variants demonstrate different transport mechanisms, security models, and languages.

## Variants

| | [TCP Observatory](tcp-observatory/) | [Unix Socket Observatory](unix-socket-observatory/) | [Rust Observatory](rust-observatory/) |
|---|---|---|---|
| **Language** | Python 3.10+ | Python 3.10+ | Rust (compiled) |
| **Transport** | HTTP over TCP socket | HTTP over AF_UNIX socket | Both (subcommands) |
| **Client identity** | IP address (`_client`) | Kernel-verified PID/UID/GID | Both |
| **Security model** | Bind to localhost | Filesystem permissions + SO_PEERCRED | Both |
| **Hook command** | `curl http://...` | `curl --unix-socket ...` | Either |
| **Multi-reader** | `tee` + FIFOs | `--output-socket` flag | `--output-socket` flag |
| **YAML highlighting** | `pygments` | `pygments` | ANSI bold (terminal-native) |
| **Server implementations** | 1 (HTTPServer) | 2 (HTTPServer + raw selectors) | 1 (raw sockets) |
| **Best for** | Getting started | IPC security concepts | Rust concepts, single binary |

## Quick Start

### TCP (simpler)

```bash
cd tcp-observatory
./server.py                    # Start server
./install-hooks.py --global    # Install hooks (other terminal)
claude                         # Watch events stream in!
```

### Unix Socket (more to learn)

```bash
cd unix-socket-observatory
./server.py                    # Start server (creates socket file)
./install-hooks.py --global    # Install hooks (other terminal)
claude                         # Watch events with PID/UID/GID!
```

### Rust (both transports in one binary)

```bash
cd rust-observatory
cargo build --release
./target/release/rust-observatory tcp --pretty-yaml   # TCP with colored YAML
./target/release/rust-observatory unix                # Unix socket mode
```

## Hook Events

Both variants capture all Claude Code hook events:

| Event | Description |
|-------|-------------|
| SessionStart | Session begins or resumes |
| UserPromptSubmit | User submits a prompt |
| PreToolUse | Before a tool executes |
| PostToolUse | After tool succeeds |
| PostToolUseFailure | After tool fails |
| PermissionRequest | Permission dialog appears |
| Notification | Claude sends notification |
| Stop | Main Claude finishes |
| SubagentStart | Subagent spawned |
| SubagentStop | Subagent finishes |
| PreCompact | Before context compaction |
| SessionEnd | Session terminates |

## Querying Logs

All variants log to `/tmp/claude/observatory/` when run via `run-with-tee-logrotator.sh`. Use `scripts/query-hooks.py` to filter:

```bash
# Which sessions are waiting for user input right now?
# Shows ALIVE/dead status by cross-referencing Stop events and /proc
./scripts/query-hooks.py --waiting

# Only alive sessions (filter out dead)
./scripts/query-hooks.py --waiting --jsonl | jq 'select(.alive)'

# Full waiting history as JSONL (pipe to jq for analysis)
./scripts/query-hooks.py --waiting=all --jsonl | jq -r '.reason' | sort | uniq -c | sort -rn

# Show last 5 PreToolUse events (human-readable)
./scripts/query-hooks.py PreToolUse -n 5

# Bash commands only, as JSONL for piping
./scripts/query-hooks.py PreToolUse --tool Bash --jsonl

# Pipe to jq for further analysis
./scripts/query-hooks.py --jsonl | jq -r '._event' | sort | uniq -c | sort -rn

# Extract just the commands being run
./scripts/query-hooks.py PreToolUse --tool Bash --jsonl | jq -r '.tool_input.command'
```

The `--waiting` flag detects dead sessions using two complementary methods:

* **Stop/SessionEnd events** — filters sessions that exited cleanly
* **/proc cross-reference** (Linux) — catches crashes, `kill -9`, or closed terminals that never emitted a Stop event

Output includes an `ALIVE`/`dead` tag and an `"alive"` boolean field in JSONL mode.

See `./scripts/query-hooks.py --help` for all options (`--tool`, `--session`, `--last`, `--file`, `--waiting`).

## Running Tests

```bash
# Python: TCP
uv run --script tcp-observatory/test_server.py -v

# Python: Unix socket
uv run --script unix-socket-observatory/test_server.py -v
uv run --script unix-socket-observatory/test_server_selectors.py -v

# JSONL fan-out
uv run --script jsonl-fanout/test_fanout.py -v

# Rust
cd rust-observatory && cargo test
```

## Documentation

* [DEVELOPER_GUIDELINES.md](DEVELOPER_GUIDELINES.md) - Hook event specs with official sources
* [tcp-observatory/docs/PIPING_EXAMPLES.md](tcp-observatory/docs/PIPING_EXAMPLES.md) - jq filters, log rotation, FIFOs, alerting recipes
* [docs/CONCURRENCY.md](docs/CONCURRENCY.md) - How servers handle parallel requests, backlog, timeouts
* [jsonl-fanout/README.md](jsonl-fanout/README.md) - JSONL fan-out daemon (ZeroMQ-inspired PUB/SUB)
* [FUTURE_WORK.md](FUTURE_WORK.md) - Roadmap

## Project Structure

```
.
├── tcp-observatory/           # Python HTTP over TCP
│   ├── server.py              # HTTPServer-based server
│   ├── install-hooks.py       # Hook installer (curl http://...)
│   ├── test_server.py         # 23 tests
│   ├── configs/               # Example hook configurations
│   └── docs/                  # Piping examples, testing guide
│
├── unix-socket-observatory/   # Python HTTP over Unix socket
│   ├── server.py              # HTTPServer + AF_UNIX override
│   ├── server_selectors.py    # Raw sockets + selectors (no HTTPServer)
│   ├── install-hooks.py       # Hook installer (curl --unix-socket)
│   ├── test_server.py         # 28 tests (HTTPServer variant)
│   ├── test_server_selectors.py  # 15 tests (selectors variant)
│   ├── SECURITY.md            # SO_PEERCRED, permissions, comparison
│   ├── configs/               # Example hook configurations
│   └── docs/                  # Piping examples, testing guide
│
├── rust-observatory/          # Rust: both TCP + Unix socket
│   ├── Cargo.toml             # Dependencies
│   ├── src/main.rs            # Single-file server (14 unit tests)
│   ├── tests/                 # 8 integration tests
│   ├── configs/               # TCP, Unix, minimal hook configs
│   └── docs/                  # Testing guide
│
├── jsonl-fanout/              # JSONL fan-out daemon (PUB/SUB)
│   ├── fanout.py              # Reads stdin, fans out to Unix socket subscribers
│   ├── subscribe.py           # Minimal subscriber client
│   └── test_fanout.py         # 6 integration tests
│
├── scripts/                   # Shared query/analysis tools
│   └── query-hooks.py         # Session states, event filtering, tmux integration (v0.6.0)
│
├── agents/                    # AI assistant guidance
├── docs/plans/                # Design documents
└── DEVELOPER_GUIDELINES.md    # Shared hook event specifications
```

## Features

### Observatory Servers

* **All 12 hook events** captured with metadata enrichment (`_ts`, `_event`, `_client`/`_peer_pid`)
* **Output formats:** JSONL (default, pipeable), pretty JSON, pretty YAML (with Pygments/ANSI highlighting)
* **No-op by design** — hooks never interfere with Claude Code; empty 200 responses let actions proceed
* **Graceful failure** — hook commands use `curl ... || true` with 0.5s timeout; server down = no impact
* **Stream separation** — stdout for JSONL data (pipe to jq/files), stderr for human-readable logs
* **Security by default** — TCP binds to `127.0.0.1`; Unix sockets use filesystem permissions (`--mode`)
* **Multi-reader streaming** (Unix/Rust) — `--output-socket` + `--tee` for concurrent `socat` readers
* **JSONL fan-out** — standalone PUB/SUB daemon; pipe any server's stdout into `fanout.py` for dynamic subscribers
* **Log rotation** — `run-with-tee-logrotator.sh` with size-based rotation (configurable via `LOG_MAX_SIZE`, `LOG_MAX_COUNT`)

### Session State Monitoring (`query-hooks.py --waiting`)

* **Real-time session states:** FRESH, PERMIT, QUESTION, IDLE, RUN:Tool, RUN:think, RUN:agent, RUN:done, DEAD
* **Dual liveness detection:**
  * Stop/SessionEnd events for clean exits
  * `/proc` cross-reference (Linux) for crashes, `kill -9`, closed terminals
* **4-layer CWD matching:** exact:start, exact:last, ancestor:start, ancestor:last
* **1-to-1 PID-to-session matching** — most recent session claims each running PID; older sessions from same directory become DEAD
* **Git-root project names** — walks upward to find `.git`, takes last 2 path components (cached); falls back to raw path
* **start_cwd preference** — uses SessionStart directory (stable) over drifted latest CWD

### Tmux Integration

* **Multi-server discovery** — scans `/tmp/tmux-$UID/` for all server sockets
* **Ancestor PID walking** — traces from claude PID up to 15 hops to find its tmux pane
* **Non-default server tagging** — `main:2.0 [ubertmux]` format for non-default servers
* **Columns:** `tmux_target`, `tmux_session`, `tmux_window`, `tmux_pane`, `tmux_cwd`

### Query & Export

* **Event filtering:** by type, tool name, session ID (prefix match), last N events
* **Output formats:** indented JSON, compact JSONL, CSV export, formatted table
* **Configurable columns:** `--columns state,ago,tmux_target,project` with `--columns-help`
* **Auto-discovery:** finds `*.log` and rotated `*.log.[0-9]*` files in `/tmp/claude/observatory/`
* **Stdin support:** pipe JSONL directly for ad-hoc analysis

### Hook Installer

* **Safe merge** — preserves existing settings, backs up before writing
* **Modes:** `--global`, `--project`, `--dry-run`, `--uninstall`
* **Diff review** — shows changes before applying
* **All 12 events** configured with appropriate curl commands per transport

### Testing

* **72+ Python tests** across TCP (23), Unix HTTPServer (28), Unix selectors (15), fan-out (6)
* **22 Rust tests** — 14 unit + 8 integration
* **uv shebang pattern** — reproducible test execution without venv setup

## License

MIT
