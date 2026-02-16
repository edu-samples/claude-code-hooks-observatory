# Unix Socket Observatory

HTTP over Unix domain socket implementation for observing Claude Code hook events. Teaches IPC security concepts (SO_PEERCRED, filesystem permissions) and multi-reader output patterns.

## Two Server Implementations

This variant includes **two servers** that behave identically but teach different concepts:

| | `server.py` | `server_selectors.py` |
|---|---|---|
| **Approach** | Overrides `HTTPServer` with AF_UNIX | Raw sockets + `selectors` event loop |
| **HTTP parsing** | Handled by `BaseHTTPRequestHandler` | Manual (`parse_http_request()`) |
| **Best for** | Familiar Python pattern | Seeing what HTTPServer hides |

## Quick Start

```bash
# Start the server (either one)
./server.py
# or: ./server_selectors.py

# In another terminal, install hooks
./install-hooks.py --global

# Start Claude Code - watch events with PID/UID/GID!
claude
```

## What You'll See

```jsonl
{"_ts":"2026-02-09T10:30:00+00:00","_event":"PreToolUse","_peer_pid":12345,"_peer_uid":1000,"_peer_gid":1000,"tool_name":"Bash","tool_input":{"command":"ls"}}
```

Note the `_peer_pid`, `_peer_uid`, `_peer_gid` fields - these come from the kernel via `SO_PEERCRED` and cannot be forged.

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
| Default socket | `./server.py` |
| Custom path | `./server.py --socket /run/user/1000/hooks.sock` |
| Environment variable | `CLAUDE_UNIX_HOOK_WATCHER=/tmp/my.sock ./server.py` |
| Custom permissions | `./server.py --mode 0600` |

Socket precedence: `--socket` > `$CLAUDE_UNIX_HOOK_WATCHER` > `/tmp/claude-observatory.sock`

## Multi-Reader Output Socket

Instead of shell-level `tee` + FIFOs, the unix socket variant has built-in multi-reader support:

```bash
# Terminal 1: Start server with output socket
./server.py --output-socket /tmp/obs-out.sock

# Terminal 2: Connect a reader
socat UNIX-CONNECT:/tmp/obs-out.sock -

# Terminal 3: Connect another reader
socat UNIX-CONNECT:/tmp/obs-out.sock - | jq '._event'

# Terminal 4: Connect yet another (all get the same stream)
socat UNIX-CONNECT:/tmp/obs-out.sock - | jq 'select(.tool_name == "Bash")'
```

Use `--tee` to send to both stdout and the output socket:

```bash
./server.py --output-socket /tmp/obs-out.sock --tee
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
uv run --script test_server_selectors.py -v
```

## Documentation

* [server.EDU_NOTES.md](server.EDU_NOTES.md) - HTTPServer override approach
* [server_selectors.EDU_NOTES.md](server_selectors.EDU_NOTES.md) - Raw selectors approach
* [install-hooks.EDU_NOTES.md](install-hooks.EDU_NOTES.md) - Installer internals
* [SECURITY.md](SECURITY.md) - SO_PEERCRED, permissions, TCP vs Unix comparison
* [docs/PIPING_EXAMPLES.md](docs/PIPING_EXAMPLES.md) - Unix socket recipes
* [docs/TESTING.md](docs/TESTING.md) - Test guide

## Comparison with TCP Variant

See the [tcp-observatory](../tcp-observatory/) for the simpler TCP/HTTP approach. Key differences:

| Aspect | Unix Socket (this) | TCP |
|--------|-------------------|-----|
| Transport | AF_UNIX socket file | TCP socket |
| Client ID | PID/UID/GID (kernel-verified) | IP address |
| Security | Filesystem permissions | Bind to localhost |
| Hook command | `curl --unix-socket ...` | `curl http://...` |
| Multi-reader | `--output-socket` (built-in) | `tee` + FIFOs |
| Port conflicts | None (uses filesystem paths) | Possible |
