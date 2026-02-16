# TCP Observatory

HTTP/TCP-based hook observatory for Claude Code. Uses Python's `http.server` stdlib module with a standard TCP socket.

## Quick Start

```bash
# 1. Start the observatory
./server.py

# 2. In another terminal, install hooks
./install-hooks.py --global

# 3. Start Claude Code - watch events stream in!
claude
```

## Output Modes

```bash
./server.py                   # Compact JSONL (default, pipeable)
./server.py --pretty-json     # Indented JSON (human-readable)
./server.py --pretty-yaml     # YAML with syntax highlighting
```

## Log Rotation

Use `run-with-tee-logrotator.sh` to see output on screen while logging to a rotating file:

```bash
./run-with-tee-logrotator.sh --pretty-yaml
# Logs rotate at 10MB, keeps 10 files in /tmp/claude/observatory/
```

## Configuration

| Method | Command |
|--------|---------|
| Default port (23518) | `./server.py` |
| Custom port | `./server.py --port 9999` |
| Environment variable | `CLAUDE_REST_HOOK_WATCHER=9999 ./server.py` |
| Network bind (dev only) | `./server.py --bind 0.0.0.0` |

Port precedence: `--port` > `$CLAUDE_REST_HOOK_WATCHER` > `23518`

## What You'll See

```jsonl
{"_ts":"2026-02-06T10:30:00+00:00","_event":"SessionStart","_client":"127.0.0.1","session_id":"abc123"}
{"_ts":"2026-02-06T10:30:01+00:00","_event":"PreToolUse","_client":"127.0.0.1","tool_name":"Bash","tool_input":{"command":"ls"}}
```

## Installing Hooks

```bash
./install-hooks.py                # Interactive
./install-hooks.py --global       # Global (~/.claude/settings.json)
./install-hooks.py --project      # Project (.claude/settings.json)
./install-hooks.py --dry-run      # Preview only
./install-hooks.py --uninstall    # Remove hooks
```

## Running Tests

```bash
uv run --script test_server.py -v
```

## Documentation

* [server.EDU_NOTES.md](server.EDU_NOTES.md) - How the server works
* [install-hooks.EDU_NOTES.md](install-hooks.EDU_NOTES.md) - How the installer works
* [docs/PIPING_EXAMPLES.md](docs/PIPING_EXAMPLES.md) - jq, tee, FIFO recipes
* [docs/TESTING.md](docs/TESTING.md) - Test guide

## Comparison with Unix Socket Variant

See the [unix-socket-observatory](../unix-socket-observatory/) for an alternative that uses Unix domain sockets instead of TCP. Key differences:

| Aspect | TCP (this) | Unix Socket |
|--------|-----------|-------------|
| Transport | TCP socket | AF_UNIX socket |
| Client ID | IP address (`_client`) | PID/UID/GID (`_peer_pid`, `_peer_uid`, `_peer_gid`) |
| Security | Bind to localhost | Filesystem permissions + SO_PEERCRED |
| Hook command | `curl http://...` | `curl --unix-socket ...` |
| Multi-reader | `tee` + FIFOs | `--output-socket` flag |
