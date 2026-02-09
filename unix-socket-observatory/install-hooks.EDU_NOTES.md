# install-hooks.py (Unix Socket) - Educational Notes

How the Unix socket hook installer works and what each curl flag does.

## The curl Command

```bash
curl -s --connect-timeout 2 --max-time 5 \
  --unix-socket /tmp/claude-observatory.sock \
  -X POST -H 'Content-Type: application/json' -d @- \
  'http://localhost/hook?event=PreToolUse' || true
```

### Flag Breakdown

| Flag | Purpose |
|------|---------|
| `-s` | Silent mode - no progress bar or error messages |
| `--connect-timeout 2` | Give up connecting after 2 seconds |
| `--max-time 5` | Total operation timeout of 5 seconds |
| `--unix-socket /tmp/...` | Connect via Unix socket instead of TCP |
| `-X POST` | HTTP POST method |
| `-H 'Content-Type: application/json'` | Tell server we're sending JSON |
| `-d @-` | Read request body from stdin (Claude Code pipes the payload) |
| `'http://localhost/...'` | URL (hostname ignored for Unix sockets) |
| `\|\| true` | If curl fails (socket missing), exit 0 anyway |

### Why `http://localhost` when using Unix sockets?

curl requires a URL even with `--unix-socket`. The hostname is completely ignored - only the socket path determines where the connection goes. `http://localhost` is conventional.

### Why `--unix-socket` instead of TCP?

* **No port conflicts** - filesystem paths don't collide like TCP ports
* **Peer credentials** - the server gets kernel-verified PID/UID/GID
* **Filesystem security** - socket permissions control access

### Why `|| true`?

Without `|| true`, when the socket file doesn't exist, curl exits with code 7. Claude Code treats non-zero hook exit as an error and shows a warning on every tool use. `|| true` makes the hook silently no-op.

## Differences from TCP install-hooks.py

| Aspect | TCP version | Unix Socket version |
|--------|------------|-------------------|
| Address parameter | `--port`, `--bind` | `--socket` |
| Default | `127.0.0.1:23518` | `/tmp/claude-observatory.sock` |
| Environment variable | `CLAUDE_REST_HOOK_WATCHER` | `CLAUDE_UNIX_HOOK_WATCHER` |
| Uninstall marker | `http://127.0.0.1:23518/hook` | `--unix-socket` |
| curl command | `curl http://host:port/...` | `curl --unix-socket path http://localhost/...` |

Everything else (merge logic, backup strategy, diff display, prompts) is identical.
