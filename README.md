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

## Running Tests

```bash
# Python: TCP
uv run --script tcp-observatory/test_server.py -v

# Python: Unix socket
uv run --script unix-socket-observatory/test_server.py -v
uv run --script unix-socket-observatory/test_server_selectors.py -v

# Rust
cd rust-observatory && cargo test
```

## Documentation

* [DEVELOPER_GUIDELINES.md](DEVELOPER_GUIDELINES.md) - Hook event specs with official sources
* [docs/CONCURRENCY.md](docs/CONCURRENCY.md) - How servers handle parallel requests, backlog, timeouts
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
├── agents/                    # AI assistant guidance
├── docs/plans/                # Design documents
└── DEVELOPER_GUIDELINES.md    # Shared hook event specifications
```

## License

MIT
