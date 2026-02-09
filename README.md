# Claude Code Hooks Observatory

An educational project for observing Claude Code hook events in real-time. Two implementations teach different IPC concepts.

## Why?

Learn how Claude Code hooks work by watching them fire in real-time. Fork this repo to build your own hook logic. Two variants demonstrate different transport mechanisms and security models.

## Variants

| | [TCP Observatory](tcp-observatory/) | [Unix Socket Observatory](unix-socket-observatory/) |
|---|---|---|
| **Transport** | HTTP over TCP socket | HTTP over AF_UNIX socket |
| **Client identity** | IP address (`_client`) | Kernel-verified PID/UID/GID |
| **Security model** | Bind to localhost | Filesystem permissions + SO_PEERCRED |
| **Hook command** | `curl http://127.0.0.1:23518/...` | `curl --unix-socket /tmp/claude-observatory.sock ...` |
| **Multi-reader** | `tee` + FIFOs (Unix pipes) | Built-in `--output-socket` flag |
| **Server implementations** | 1 (HTTPServer) | 2 (HTTPServer + raw selectors) |
| **Best for** | Getting started, simplicity | Learning IPC security, seeing internals |

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
# All tests (both variants)
uv run --script tcp-observatory/test_server.py -v
uv run --script unix-socket-observatory/test_server.py -v

# TCP only
uv run --script tcp-observatory/test_server.py -v

# Unix socket only
uv run --script unix-socket-observatory/test_server.py -v
uv run --script unix-socket-observatory/test_server_selectors.py -v
```

## Documentation

* [DEVELOPER_GUIDELINES.md](DEVELOPER_GUIDELINES.md) - Hook event specs with official sources
* [FUTURE_WORK.md](FUTURE_WORK.md) - Roadmap

## Project Structure

```
.
├── tcp-observatory/           # HTTP over TCP implementation
│   ├── server.py              # HTTPServer-based server
│   ├── install-hooks.py       # Hook installer (curl http://...)
│   ├── test_server.py         # Tests
│   ├── configs/               # Example hook configurations
│   └── docs/                  # Piping examples, testing guide
│
├── unix-socket-observatory/   # HTTP over Unix socket implementation
│   ├── server.py              # HTTPServer + AF_UNIX override
│   ├── server_selectors.py    # Raw sockets + selectors (no HTTPServer)
│   ├── install-hooks.py       # Hook installer (curl --unix-socket)
│   ├── test_server.py         # Tests for HTTPServer variant
│   ├── test_server_selectors.py  # Tests for selectors variant
│   ├── SECURITY.md            # SO_PEERCRED, permissions, comparison
│   ├── configs/               # Example hook configurations
│   └── docs/                  # Piping examples, testing guide
│
├── agents/                    # AI assistant guidance
├── docs/plans/                # Design documents
└── DEVELOPER_GUIDELINES.md    # Shared hook event specifications
```

## License

MIT
