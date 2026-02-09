# Rust Observatory Design

Date: 2026-02-09

## Context

The repository had two Python observatory variants (TCP and Unix socket). This adds a third variant: a compiled Rust binary that supports both transports in a single binary, teaching Rust-specific systems programming concepts.

## Goals

1. Single binary supporting both TCP and Unix socket transports via subcommands
2. Colored YAML output using syntect (matching Python's pygments)
3. SO_PEERCRED via raw libc FFI (educational: shows the unsafe boundary)
4. Educational single-file design with clear section organization
5. Full test coverage: unit tests in main.rs + integration tests as subprocess

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Both transports | `tcp` / `unix` subcommands | Single binary, demonstrates Rust enums |
| HTTP layer | Raw std sockets + manual parsing | Educational (like Python server_selectors.py) |
| Transport abstraction | `enum PeerInfo { Tcp, Unix, Unknown }` | Teaches Rust enums with data |
| Connection handler | `impl Read + Write` generic | Works for both stream types, zero-cost |
| YAML highlighting | `syntect` crate | Rust standard for syntax highlighting |
| SO_PEERCRED | Raw `libc` FFI with `#[cfg]` | Shows unsafe boundary, platform-specific |
| CLI | `clap` derive API with subcommands | Clean, declarative, standard |
| YAML serialization | `serde_yaml` 0.9 | Well-known API (noted as archived in EDU_NOTES) |
| Default TCP port | 23519 | Avoids collision with Python's 23518 |
| Default socket path | `/tmp/claude-observatory-rust.sock` | Avoids collision with Python |
| Socket cleanup | `Drop` trait guard | More reliable than try/finally |
| Signal handling | Raw `libc::signal` | Educational, no ctrlc crate dependency |
| Non-blocking accept | `set_nonblocking(true)` on listener | Enables graceful shutdown via flag check |

## Structure

```
rust-observatory/
├── Cargo.toml                   # 7 dependencies
├── Cargo.lock                   # Locked for reproducibility
├── src/main.rs                  # Single-file server (~890 lines with tests)
├── tests/integration_tests.rs   # 8 subprocess-based tests
├── README.md                    # Quick start, comparison table
├── server.EDU_NOTES.md          # Rust-specific concepts
├── configs/
│   ├── hooks-tcp.json           # TCP config (port 23519)
│   ├── hooks-unix.json          # Unix socket config
│   └── hooks-minimal.json       # Just PreToolUse
└── docs/
    └── TESTING.md               # cargo test + manual curl guide
```

## Test Summary

* `src/main.rs` unit tests: 14 tests (HTTP parsing, enrichment, formatting)
* `tests/integration_tests.rs`: 8 tests (TCP + Unix subprocess tests)
* Total: 22 tests

## Platform Notes

SO_PEERCRED uses `#[cfg(target_os)]` for Linux (getsockopt) and macOS (getpeereid). Falls back to `PeerInfo::Unknown` on other platforms. Tests conditionally verify peer credentials on Linux.

Key lesson carried from Python: Linux returns `(0, -1, -1)` for non-AF_UNIX sockets via getsockopt SO_PEERCRED instead of an error. Must validate `pid > 0`.
