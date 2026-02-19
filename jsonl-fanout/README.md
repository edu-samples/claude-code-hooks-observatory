# JSONL Fan-Out Daemon

ZeroMQ-inspired PUB/SUB for JSONL streams over Unix sockets.

Reads JSONL lines from stdin and fans them out to all connected subscribers.
Subscribers connect/disconnect freely — slow or dead subscribers are dropped
immediately (no buffering), matching ZeroMQ PUB socket semantics.

## Architecture

```mermaid
graph LR
    S[server.py<br>stdout] --> T[tee -a log.jsonl]
    T --> F[fanout.py]
    F -->|Unix socket| S1[subscriber 1<br>jq filter]
    F -->|Unix socket| S2[subscriber 2<br>dashboard]
    F -->|Unix socket| S3[subscriber N<br>query-hooks.py]
```

## Quick Start

```bash
# Terminal 1: Start fan-out with an observatory server
./tcp-observatory/server.py | tee -a /tmp/claude/observatory/tcp.log | ./jsonl-fanout/fanout.py

# Terminal 2: Subscribe and filter
./jsonl-fanout/subscribe.py | jq 'select(._event == "PreToolUse")'

# Terminal 3: Subscribe and watch tool names
./jsonl-fanout/subscribe.py | jq -r '.tool_name // empty'

# Terminal 4: Raw subscribe with socat (no Python needed)
socat - UNIX-CONNECT:/tmp/claude-fanout.sock
```

## CLI Reference

### fanout.py

```
fanout.py [--socket PATH] [--mode OCTAL] [--stats]

Options:
  --socket PATH    Unix socket path (default: $CLAUDE_FANOUT_SOCKET or /tmp/claude-fanout.sock)
  --mode OCTAL     Socket file permissions (default: 0660)
  --stats          Print periodic subscriber stats to stderr
```

### subscribe.py

```
subscribe.py [--socket PATH]

Options:
  --socket PATH    Unix socket path (default: $CLAUDE_FANOUT_SOCKET or /tmp/claude-fanout.sock)
```

## Composition Examples

### With TCP Observatory

```bash
./tcp-observatory/server.py | tee -a tcp.log | ./jsonl-fanout/fanout.py
```

### With Unix Socket Observatory

```bash
./unix-socket-observatory/server.py --tee --output-socket /tmp/obs-out.sock &
socat UNIX-CONNECT:/tmp/obs-out.sock - | ./jsonl-fanout/fanout.py
```

### With Rust Observatory

```bash
./rust-observatory/target/release/observatory | tee -a rust.log | ./jsonl-fanout/fanout.py
```

### Custom Socket Path via Environment

```bash
export CLAUDE_FANOUT_SOCKET=/run/user/$(id -u)/claude-fanout.sock
./jsonl-fanout/fanout.py  # uses $CLAUDE_FANOUT_SOCKET
./jsonl-fanout/subscribe.py  # same
```

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Input | stdin only | Pipe composition; no protocol needed |
| Transport | Unix socket | No port conflicts, filesystem permissions |
| Backpressure | Drop slow subscribers | ZeroMQ PUB semantics; simplest correct behavior |
| Event loop | selectors | Single-threaded, no locks, matches repo patterns |
| Protocol | Raw JSONL stream | No handshake, no framing — connect and receive |

## Testing

```bash
uv run --script jsonl-fanout/test_fanout.py -v
```

## How It Works

The daemon uses Python's `selectors` module to monitor two file descriptors in a single thread:

1. **stdin** (non-blocking) — incoming JSONL lines
2. **listener socket** — new subscriber connections

When a complete line arrives on stdin, `fan_out()` attempts `sendall()` to each subscriber. Any subscriber that raises `BrokenPipeError` or `OSError` is immediately disconnected — no retry, no buffering. This matches ZeroMQ's PUB socket behavior where slow consumers are dropped rather than causing backpressure on the publisher.

**Non-blocking stdin subtlety:** `os.read()` on a non-blocking fd returns whatever bytes are available — not necessarily a complete line. The daemon accumulates bytes in a buffer and splits on `\n` to extract complete lines. This is a common pattern when combining non-blocking I/O with line-oriented protocols.
