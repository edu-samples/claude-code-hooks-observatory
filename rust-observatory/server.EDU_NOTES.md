# Server Educational Notes (Rust)

How `src/main.rs` works and why it's written this way.

## Why Rust?

This implementation teaches concepts that don't exist in Python:

* **Enums with data** - `PeerInfo::Tcp { client_addr }` vs `PeerInfo::Unix { pid, uid, gid }` replaces Python's dictionary juggling with compile-time type safety
* **Unsafe FFI boundary** - `get_peer_creds()` shows exactly where safe Rust meets raw C (`libc::getsockopt`)
* **Drop trait** - `SocketCleanup` struct ensures socket files are cleaned up even on panic (more reliable than Python's `try`/`finally`)
* **Generic functions** - `handle_connection(stream: &mut impl Read + Write)` works for both `TcpStream` and `UnixStream` without trait objects
* **Zero-cost abstractions** - The `OutputMode` enum dispatches formatting at runtime without virtual function overhead

## Architecture: Single File, Seven Sections

The file is organized in clear sections, each marked with `// === SECTION NAME ===`:

```
CLI DEFINITIONS      → clap derive structs (what the user types)
CONSTANTS            → defaults, env var names
OUTPUT FORMATTING    → JSONL, pretty JSON, pretty YAML
HTTP PARSING         → manual request/response parsing
TIMESTAMPS/ENRICH   → PeerInfo enum, metadata enrichment
SO_PEERCRED          → raw libc FFI for peer credentials
OUTPUT MANAGER       → multi-reader output socket pattern
SOCKET CLEANUP       → Drop guard for socket files
CONNECTION HANDLING  → generic stream handler
MAIN                 → transport dispatch, event loop
TESTS                → #[cfg(test)] unit tests
```

## Key Concepts

### Enums vs Inheritance

Python uses dictionaries and `isinstance()` checks. Rust uses enums:

```rust
enum PeerInfo {
    Tcp { client_addr: String },     // TCP: we know the IP
    Unix { pid: i32, uid: u32, gid: u32 },  // Unix: kernel-verified identity
    Unknown,                          // Fallback
}
```

The `match` statement in `enrich_payload()` is exhaustive - the compiler ensures we handle every variant. Forgetting a case is a compile error, not a runtime bug.

### Generic Connection Handling

Both `TcpStream` and `UnixStream` implement `Read + Write`. Instead of duplicating the handler:

```rust
fn handle_connection(stream: &mut (impl Read + Write), ...) { ... }
```

This is monomorphized at compile time - no virtual dispatch overhead. The compiler generates specialized versions for each stream type.

### libc FFI for SO_PEERCRED

The `get_peer_creds()` function uses raw `libc` calls intentionally (not the higher-level `nix` crate) to show the FFI boundary:

```rust
let ret = unsafe {
    libc::getsockopt(
        fd,
        libc::SOL_SOCKET,
        libc::SO_PEERCRED,
        &mut ucred as *mut _ as *mut libc::c_void,
        &mut len,
    )
};
```

Key lesson from the Python implementation: Linux returns `(0, -1, -1)` for non-AF_UNIX sockets instead of an error. We validate `pid > 0` before trusting the result.

Platform-specific code uses `#[cfg(target_os)]`:

```rust
#[cfg(target_os = "linux")]  { /* SO_PEERCRED */ }
#[cfg(target_os = "macos")]  { /* getpeereid() */ }
// Falls through to PeerInfo::Unknown on other platforms
```

### YAML Highlighting with syntect

`syntect` is Rust's equivalent of Python's `pygments`. Key difference: we load syntax definitions and themes **once at startup** and reuse them:

```rust
struct YamlHighlighter {
    syntax_set: SyntaxSet,   // All syntax definitions (expensive to load)
    theme_set: ThemeSet,     // All color themes
}
```

The `highlight()` method produces ANSI escape codes for terminal coloring. When stdout isn't a TTY (piped), we output plain YAML without colors.

### Drop Guard for Cleanup

Python uses `try`/`finally` or `atexit`. Rust uses the `Drop` trait:

```rust
struct SocketCleanup { path: String }

impl Drop for SocketCleanup {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}
```

This runs even on panic, stack unwinding, or early return. The `_cleanup` variable in `main()` keeps the guard alive for the duration of the server.

### Non-blocking Accept Loop

Both TCP and Unix listeners are set to non-blocking mode:

```rust
listener.set_nonblocking(true)?;

while running.load(Ordering::SeqCst) {
    match listener.accept() {
        Ok((mut stream, _)) => {
            stream.set_nonblocking(false);  // But reads are blocking
            handle_connection(&mut stream, ...);
        }
        Err(ref e) if e.kind() == WouldBlock => {
            thread::sleep(Duration::from_millis(50));
        }
        Err(_) => continue,
    }
}
```

The listener is non-blocking so we can check the shutdown flag between accepts. Accepted connections are set back to blocking for reliable reads.

## Dependencies

| Crate | Purpose | Python equivalent |
|-------|---------|-------------------|
| `clap` | CLI argument parsing | `argparse` |
| `serde` + `serde_json` | JSON serialization | `json` stdlib |
| `serde_yaml` | YAML serialization | `pyyaml` |
| `chrono` | Timestamps | `datetime` stdlib |
| `syntect` | Syntax highlighting | `pygments` |
| `libc` | Raw C function bindings | `socket`/`struct` stdlib |

Note: `serde_yaml` 0.9 is archived (the author deprecated it). For production use, consider `serde_yml` or manual YAML formatting. For this educational project, 0.9 works fine and the API is well-documented.

## Concurrency & Parallel Requests

The Rust server uses a single-threaded non-blocking accept loop with blocking connection handling:

```rust
listener.set_nonblocking(true);           // Listener: non-blocking (can check shutdown flag)

while running.load(Ordering::SeqCst) {
    match listener.accept() {
        Ok((mut stream, _)) => {
            stream.set_nonblocking(false); // Connection: blocking (reliable reads)
            handle_connection(&mut stream, ...);
        }
        Err(WouldBlock) => sleep(50ms),   // No connection ready, poll again
    }
}
```

**Listen backlog**: 128 (Rust std default, hardcoded in the stdlib, not configurable via API -- see [rust#55614](https://github.com/rust-lang/rust/issues/55614)). This matches what we set explicitly on the Python servers.

**Platform gotcha with non-blocking inheritance**: On Linux, `accept4()` does NOT inherit the listener's `O_NONBLOCK` flag onto accepted connections. On BSD/macOS, standard `accept()` DOES inherit it. The explicit `set_nonblocking(false)` after accept makes behavior consistent across platforms.

**50ms sleep tradeoff**: When no connections are pending, the server sleeps 50ms before polling again. This adds 0-50ms jitter to the first request after idle, but avoids burning CPU. An alternative would be `poll()`/`epoll()` to block until data arrives, but that adds complexity for negligible benefit at hook event rates.

**stdout and SIGKILL**: After `print!()` + `flush()`, data is in the kernel pipe buffer (64KB on Linux) and survives process death. Rust's `LineWriter` on stdout has a 1024-byte userspace buffer -- if SIGKILL arrives between `print!()` and `flush()`, that buffer is lost (Drop doesn't run on SIGKILL). Since we flush after every event, at most one event could be lost.

See [../docs/CONCURRENCY.md](../docs/CONCURRENCY.md) for the full cross-variant analysis.

## What This Doesn't Do

Following the "no-op by default" principle:

* Never modifies Claude Code's behavior (empty 200 responses)
* Never blocks or delays hook processing
* Single-threaded (educational simplicity over production throughput)
* No TLS, authentication, or access control beyond socket permissions
