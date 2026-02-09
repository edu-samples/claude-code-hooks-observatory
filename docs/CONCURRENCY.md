# Concurrency & Parallel Request Handling

How the observatory servers handle multiple simultaneous hook events, and why the current design is safe for Claude Code usage.

## The Short Answer

All four servers are **single-threaded**. Parallel requests queue in the kernel's listen backlog (128 connections) and are processed one-at-a-time. No data is lost unless 129+ hooks fire simultaneously (effectively impossible). Curl timeouts of 0.5s connect / 1s total ensure Claude Code never stalls for long even if the observatory is down.

## How Single-Threaded Servers Handle Concurrency

### The Listen Backlog

When a server calls `listen(128)`, it tells the kernel: "hold up to 128 completed connections in a queue while I'm busy handling the current one."

```
Hook event 1 ──→ ┌──────────────────────────────────┐
Hook event 2 ──→ │  Kernel Listen Backlog (max 128)  │ ──→ accept() ──→ handle ──→ respond
Hook event 3 ──→ │  [conn1] [conn2] [conn3] ...      │
     ...         └──────────────────────────────────┘
Hook event 129 → ✗ ECONNREFUSED (queue full)
```

The key insight: **the kernel does the queuing, not the application.** The server just calls `accept()` in a loop. Between accepts, connections wait in the kernel queue with their data already buffered in per-socket receive buffers.

### What's Buffered

Once a connection enters the backlog:

1. **TCP**: The 3-way handshake completes. The client sees `connect()` succeed.
2. **AF_UNIX**: The `connect()` call returns success immediately.
3. **Client sends data**: POST body goes into the kernel's per-socket receive buffer (~128KB on Linux).
4. **Server eventually `accept()`s**: Reads the already-buffered data with zero additional latency.

No data is lost within an accepted connection. The only loss vector is the connection itself being refused when the backlog is full.

### Processing Time

Each hook request takes approximately 1-5ms to process:

* Parse HTTP request (~0.1ms)
* Parse JSON body (~0.1ms)
* Enrich with metadata (~0.01ms)
* Format output (~0.1ms for JSONL, ~1ms for pretty-yaml with syntax highlighting)
* Write to stdout + flush (~0.1ms)
* Send HTTP response (~0.1ms)

At ~2ms per request, the server can drain 50 queued requests per 100ms. A backlog of 128 provides ample headroom.

## Backlog Configuration

### Why 128?

| Previous (default) | Current | Rationale |
|---|---|---|
| 5 (Python `socketserver` default) | 128 | Matches Linux `somaxconn` default and Rust's std default |

The default of 5 was a relic from early TCP implementations. Modern Linux defaults `somaxconn` to 128 (or 4096 on newer kernels). Setting backlog=128 costs nothing -- it's just a hint to the kernel about queue size.

### Platform Behavior

| Platform | Backlog Clamp | Notes |
|---|---|---|
| Linux | `min(app_value, /proc/sys/net/core/somaxconn)` | Default somaxconn=128, some distros set 4096 |
| macOS | `min(app_value, kern.ipc.somaxconn)` | Default 128 |
| Rust std | Hardcoded 128 in stdlib, not configurable via API | [rust#55614](https://github.com/rust-lang/rust/issues/55614) |

### Per-Server Implementation

| Server | How backlog is set |
|---|---|
| TCP Python | `HTTPServer.request_queue_size = 128` (class attribute) |
| Unix HTTPServer Python | `request_queue_size = 128` on `UnixHTTPServer` class |
| Unix Selectors Python | `input_sock.listen(128)` directly |
| Rust (both modes) | Rust std default (128), not configurable |

## Curl Timeout Configuration

### The Hook Command

```bash
curl -s --connect-timeout 0.5 --max-time 1 -X POST -H 'Content-Type: application/json' -d @- 'http://...' || true
```

### Why These Values?

| Flag | Value | Rationale |
|---|---|---|
| `--connect-timeout 0.5` | 500ms | Localhost/local socket connect is <1ms. 500ms covers pathological kernel scheduling delays. |
| `--max-time 1` | 1s | Total timeout including connect + send + wait for response. 1s is generous for a local round-trip. |
| `\|\| true` | - | If curl fails for any reason, exit 0 so Claude Code proceeds normally. |

### What Happens When the Observatory Is Down?

| Scenario | Behavior | Time blocked |
|---|---|---|
| Server not running (TCP) | `connect()` gets `ECONNREFUSED` instantly | ~0ms |
| Server not running (Unix socket) | `connect()` fails with "No such file" | ~0ms |
| Server running, backlog full (TCP) | Kernel drops SYN, curl retries until `--connect-timeout` | **500ms** |
| Server running, backlog full (Unix) | `connect()` gets `EAGAIN` instantly | ~0ms |
| Connection accepted, server slow | curl waits for HTTP response until `--max-time` | **up to 1s** |

**Worst case**: Claude Code stalls for 1 second per hook event, then `|| true` lets it continue. With the observatory running normally, hooks add <5ms of latency.

### Why `--max-time` Matters

Without `--max-time`, this scenario could stall Claude Code:

1. Connection enters the backlog (TCP handshake completes)
2. curl sends the POST body
3. Server is busy processing another request
4. curl waits indefinitely for the HTTP response
5. Claude Code is frozen waiting for the hook command to finish

`--max-time 1` caps this at 1 second.

## Server-Specific Concurrency Details

### TCP Observatory (Python `http.server`)

* **Event loop**: `socketserver.BaseServer.serve_forever()` uses `selectors.PollSelector` (Linux) or `SelectSelector` (fallback)
* **Poll interval**: 0.5 seconds (hardcoded in `serve_forever`)
* **Request handling**: `_handle_request_noblock()` -- despite the name, it **blocks** during `process_request()`. The "noblock" refers only to the `accept()` call.
* **Thread safety**: Single-threaded, no locks needed. `print(flush=True)` is atomic for messages under 4096 bytes (POSIX `PIPE_BUF` guarantee on Linux).

### Unix HTTPServer (Python `http.server` + AF_UNIX)

* Same event loop as TCP, with additional `service_actions()` callback for OutputManager polling
* `service_actions()` runs after each request and on each poll interval (100ms), accepting pending output socket readers
* **OutputManager concern**: `sendall()` to output socket clients is blocking. A stalled output reader could delay hook processing. Dead clients are detected on write failure and removed.

### Unix Selectors (Python `selectors`)

* **Event loop**: `selectors.DefaultSelector` -- uses `epoll` on Linux, `kqueue` on macOS
* **Connection handling**: Despite using selectors (which supports concurrent I/O), connections are processed **synchronously** within the event callback. The selector is used for the accept/poll pattern, not for concurrent request handling.
* **Partial read handling**: Loops on `recv()` until `Content-Length` bytes are read. No timeout on this loop -- a slow client sending partial data could stall the server.

### Rust Observatory

* **Listener**: Non-blocking (`set_nonblocking(true)`) so the shutdown flag can be checked between accepts
* **Accepted connections**: Set back to **blocking** (`set_nonblocking(false)`) for reliable reads
* **Platform note on non-blocking inheritance**: Linux's `accept4()` does NOT inherit the listener's non-blocking state. BSD/macOS `accept()` DOES. The explicit `set_nonblocking(false)` after accept makes behavior consistent across platforms.
* **50ms sleep**: When no connections are pending (`WouldBlock`), the server sleeps 50ms before polling again. This adds 0-50ms latency to the first request after an idle period but avoids CPU spinning.
* **Read buffer**: 64KB initial read. If body exceeds initial read, loops reading 64KB chunks until `Content-Length` is satisfied.

## Stdout Atomicity & SIGKILL

### Single-Threaded Writes

All servers write one JSONL line per event via `print(flush=True)` (Python) or `print!()` + `flush()` (Rust). Since they're single-threaded, output is never interleaved.

POSIX guarantees that writes up to `PIPE_BUF` (4096 bytes on Linux) are atomic. Typical JSONL hook events are <1KB, well under this limit.

### What Happens on SIGKILL?

| Buffer Layer | Size | SIGKILL Behavior |
|---|---|---|
| Python `sys.stdout` buffer | Line-buffered (with `flush=True`) | `flush=True` forces write before return |
| Rust `BufWriter` (stdout) | 1024 bytes (LineWriter) | Lost if not flushed. `flush()` after each event makes this safe. |
| Kernel pipe buffer | 64KB (Linux) | **Survives** process death. Reader can still consume. |

After a successful `flush()` / `print(flush=True)`, data is in the kernel pipe buffer and survives SIGKILL. The observatory servers flush after every event, so the only risk is the event being processed at the exact moment of kill -- which loses at most one event.

## Practical Implications

### "How Many Parallel Hooks Can I Have?"

* **128 simultaneous** before any are refused (all servers)
* In practice, Claude Code rarely fires more than 2-3 hooks in parallel (e.g., a subagent starting while a tool completes)
* Even with aggressive parallelism (10 subagents), each hook takes ~2ms to process, so the queue drains faster than it fills

### "Should I Add Threading?"

For the observatory's educational purpose: **no**. Threading adds complexity (locks around stdout, potential JSONL interleaving) without meaningful benefit. Claude Code's hook event rate is well within single-threaded capacity.

If you're forking this for production use with high event rates, consider:

* Python: `socketserver.ThreadingMixIn` + `threading.Lock()` around output writes
* Rust: `tokio` async runtime or `std::thread::spawn` per connection

### "What If I Have Slow Output Socket Readers?"

This is the most realistic risk. If you connect a reader via `--output-socket` that doesn't consume data, `sendall()` to that reader will block, stalling all hook processing. The servers detect broken readers (via write errors) but can't detect slow readers until the kernel buffer fills.

Mitigation: always use non-blocking readers, or set a read deadline. If a reader disconnects, the server cleans it up on the next write attempt.
