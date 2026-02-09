# Unix Socket Guide

Guidance for working on the unix-socket-observatory variant.

## Key Concepts

### AF_UNIX Sockets

Unix domain sockets use filesystem paths instead of IP:port. They're local-only (no network), faster than TCP loopback, and support kernel-level peer identification.

### SO_PEERCRED (Linux) / LOCAL_PEERCRED (macOS)

The kernel can tell you exactly which process connected: PID, UID, GID. This is unforgeable - unlike TCP where any local process can connect from 127.0.0.1.

### Two Server Implementations

* `server.py` - Overrides `HTTPServer` with `AF_UNIX`. Familiar pattern, minimal delta from TCP.
* `server_selectors.py` - Raw sockets + `selectors` module. Shows what HTTPServer hides.

Both must behave identically from the outside (same HTTP protocol, same enrichment, same output).

## Implementation Rules

### Socket Lifecycle

1. Create socket file on startup
2. Set permissions via `--mode` flag (default `0o660`)
3. Clean up (unlink) socket file on shutdown
4. Handle stale socket files from crashed processes

### Output Socket Pattern

The `--output-socket` flag creates a second Unix socket for readers:

* Server creates listener socket at the output path
* Readers connect to receive JSONL output
* `--tee` sends to both stdout and output socket
* Without `--tee`, output goes only to the output socket

### Peer Credentials

* Always attempt `SO_PEERCRED` (Linux) first
* Fall back to `LOCAL_PEERCRED` (macOS)
* Return `None` on unsupported platforms
* Add `_peer_pid`, `_peer_uid`, `_peer_gid` to enriched payload (or omit if unavailable)

### Compatibility

* `curl --unix-socket` is widely available (curl 7.40+, 2015)
* The `http://localhost` URL in curl is required but ignored for routing - only the socket path matters
* Tests must use raw `socket.AF_UNIX` connections since `http.client` doesn't support Unix sockets

## Educational Priority

This variant exists to teach. Every design choice should be explainable. If something is complex, add an educational comment explaining *why* it exists, not just *what* it does.
