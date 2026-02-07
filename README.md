# Claude Code Hooks Observatory

A transparent, educational REST server for observing Claude Code hook events in real-time.

## Why?

Learn how Claude Code hooks work by watching them fire in real-time. Fork this repo to build your own hook logic.

## Quick Start

```bash
# 1. Start the observatory
./server.py

# 2. In another terminal, install hooks
./install-hooks.py --global

# 3. Start Claude Code - watch events stream in!
claude
```

## What You'll See

```jsonl
{"_ts":"2026-02-06T10:30:00+00:00","_event":"SessionStart","_client":"127.0.0.1","session_id":"abc123","source":"startup"}
{"_ts":"2026-02-06T10:30:01+00:00","_event":"PreToolUse","_client":"127.0.0.1","tool_name":"Bash","tool_input":{"command":"ls"}}
{"_ts":"2026-02-06T10:30:02+00:00","_event":"PostToolUse","_client":"127.0.0.1","tool_name":"Bash","tool_response":{...}}
```

## Configuration

| Method | Command |
|--------|---------|
| Default port (23518) | `./server.py` |
| Custom port | `./server.py --port 9999` |
| Environment variable | `CLAUDE_REST_HOOK_WATCHER=9999 ./server.py` |
| Network bind (dev only) | `./server.py --bind 0.0.0.0` |

Port precedence: `--port` > `$CLAUDE_REST_HOOK_WATCHER` > `23518`

## Installing Hooks

```bash
# Interactive mode
./install-hooks.py

# Global installation
./install-hooks.py --global

# Project-only installation
./install-hooks.py --project

# Preview without writing
./install-hooks.py --dry-run

# Remove observatory hooks
./install-hooks.py --uninstall
```

## Hook Events

The observatory captures all Claude Code hook events:

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

## Documentation

* [DEVELOPER_GUIDELINES.md](DEVELOPER_GUIDELINES.md) - Hook specs with official sources
* [docs/PIPING_EXAMPLES.md](docs/PIPING_EXAMPLES.md) - jq, tee, FIFO recipes
* [docs/TESTING.md](docs/TESTING.md) - Running tests
* [FUTURE_WORK.md](FUTURE_WORK.md) - Roadmap

## Running Tests

```bash
uv run --script test_server.py
```

## Project Structure

```
.
├── server.py              # Main HTTP server (stdlib only)
├── install-hooks.py       # Hook configuration installer
├── test_server.py         # pytest tests
├── configs/               # Example hook configurations
│   ├── hooks-global.json
│   ├── hooks-project.json
│   └── hooks-minimal.json
└── docs/
    ├── PIPING_EXAMPLES.md
    └── TESTING.md
```

## License

MIT
