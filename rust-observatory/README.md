# Rust Observatory

A single Rust binary that supports **both TCP and Unix socket** transports for observing Claude Code hook events. Demonstrates Rust-specific concepts: enums for transport abstraction, raw `libc` FFI for SO_PEERCRED, `syntect` for syntax highlighting, and ownership patterns for resource lifecycle.

## Quick Start

```bash
# Build
cargo build --release

# TCP mode (port 23519)
./target/release/rust-observatory tcp

# Unix socket mode
./target/release/rust-observatory unix

# With colored YAML output
./target/release/rust-observatory tcp --pretty-yaml
```

Then configure Claude Code hooks using one of the provided configs:

```bash
# Copy TCP config to Claude Code global settings
cp configs/hooks-tcp.json ~/.claude/settings.json

# Or Unix socket config
cp configs/hooks-unix.json ~/.claude/settings.json
```

## Transport Modes

### TCP (like tcp-observatory)

```bash
./target/release/rust-observatory tcp                    # Default: 127.0.0.1:23519
./target/release/rust-observatory tcp --port 9999        # Custom port
./target/release/rust-observatory tcp --bind 0.0.0.0     # All interfaces (dev only)
```

Port precedence: `--port` > `$CLAUDE_RUST_HOOK_WATCHER` > `23519`

### Unix Socket (like unix-socket-observatory)

```bash
./target/release/rust-observatory unix                             # Default path
./target/release/rust-observatory unix --socket /tmp/my.sock       # Custom path
./target/release/rust-observatory unix --mode 0600                 # Restrictive perms
./target/release/rust-observatory unix --output-socket /tmp/o.sock # Multi-reader
./target/release/rust-observatory unix --output-socket /tmp/o.sock --tee  # stdout + socket
```

Socket precedence: `--socket` > `$CLAUDE_RUST_UNIX_HOOK_WATCHER` > `/tmp/claude-observatory-rust.sock`

## Output Modes

```bash
./target/release/rust-observatory tcp                 # Compact JSONL (default, pipeable)
./target/release/rust-observatory tcp --pretty-json   # Indented JSON
./target/release/rust-observatory tcp --pretty-yaml   # YAML with syntax highlighting
```

All three modes work with both `tcp` and `unix` subcommands.

## What You'll See

### TCP mode

```jsonl
{"_ts":"2026-02-09T10:30:00+00:00","_event":"PreToolUse","_client":"127.0.0.1","tool_name":"Bash","tool_input":{"command":"ls"}}
```

### Unix mode (includes peer credentials)

```jsonl
{"_ts":"2026-02-09T10:30:00+00:00","_event":"PreToolUse","_peer_pid":12345,"_peer_uid":1000,"_peer_gid":1000,"tool_name":"Bash","tool_input":{"command":"ls"}}
```

## Running Tests

```bash
cargo test           # All tests (14 unit + 8 integration)
cargo test -- -v     # Verbose output
```

## Comparison with Python Variants

| Aspect | TCP (Python) | Unix (Python) | Rust (this) |
|--------|-------------|---------------|-------------|
| Transport | TCP only | Unix only | Both (subcommands) |
| Language | Python 3.10+ | Python 3.10+ | Rust (compiled) |
| HTTP layer | `http.server` stdlib | HTTPServer or raw selectors | Raw `std` sockets |
| YAML highlighting | `pygments` | `pygments` | `syntect` |
| Peer credentials | N/A | `SO_PEERCRED` (Python) | `SO_PEERCRED` (libc FFI) |
| Default port | 23518 | N/A | 23519 |
| Default socket | N/A | `/tmp/claude-observatory.sock` | `/tmp/claude-observatory-rust.sock` |
| Dependencies | stdlib only | stdlib only | clap, serde, syntect, chrono, libc |
| Install | `uv run --script` | `uv run --script` | `cargo build --release` |

## Documentation

* [server.EDU_NOTES.md](server.EDU_NOTES.md) - Rust-specific concepts and architecture
* [docs/TESTING.md](docs/TESTING.md) - Test guide with cargo test and manual curl examples
