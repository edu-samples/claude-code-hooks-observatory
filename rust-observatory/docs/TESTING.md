# Testing (Rust)

## Quick Start

```bash
cargo test           # All tests
cargo test -- -v     # Verbose output
```

## Test Structure

### Unit tests (src/main.rs, 14 tests)

| Test | What it verifies |
|------|-----------------|
| `test_parse_http_request_post` | POST with JSON body parsed correctly |
| `test_parse_http_request_get` | GET /health parsed correctly |
| `test_parse_http_request_empty_body` | POST with empty body |
| `test_build_http_response_200` | HTTP 200 response format |
| `test_build_http_response_200_with_body` | Response with JSON body |
| `test_build_http_response_404` | HTTP 404 response format |
| `test_parse_query_string` | Multi-param query string |
| `test_parse_query_string_single` | Single param query string |
| `test_parse_query_string_empty` | Empty query string |
| `test_enrich_payload_tcp` | Adds _ts, _event, _client |
| `test_enrich_payload_unix` | Adds _ts, _event, _peer_pid/uid/gid |
| `test_get_timestamp_format` | ISO 8601 format with timezone |
| `test_format_event_jsonl` | Compact single-line JSON |
| `test_format_event_pretty_json` | Indented multi-line JSON |

### Integration tests (tests/integration_tests.rs, 8 tests)

| Test | What it verifies |
|------|-----------------|
| `test_tcp_health_returns_ok` | GET /health via TCP returns `{"status":"ok"}` |
| `test_tcp_hook_returns_200` | POST /hook via TCP returns empty 200 |
| `test_tcp_outputs_enriched_jsonl` | Stdout contains enriched JSONL with _client |
| `test_tcp_404_for_get_hook` | GET /hook returns 404 (POST only) |
| `test_unix_health_returns_ok` | GET /health via Unix socket |
| `test_unix_hook_returns_200` | POST /hook via Unix socket |
| `test_unix_peer_credentials` | Stdout contains _peer_pid/_peer_uid/_peer_gid |
| `test_unix_multiple_events` | Four sequential events all recorded |

## Running Specific Tests

```bash
# Single test
cargo test test_tcp_health_returns_ok

# Tests matching pattern
cargo test test_unix

# Only unit tests
cargo test --lib

# Only integration tests
cargo test --test integration_tests
```

## How Integration Tests Work

### Subprocess Pattern

Integration tests spawn the binary as a subprocess (same pattern as Python's `test_server_selectors.py`):

```rust
let child = Command::new(binary_path())
    .arg("tcp")
    .arg("--port")
    .arg(port.to_string())
    .stdout(Stdio::piped())
    .stderr(Stdio::piped())
    .spawn()?;
```

### Unique Ports/Paths

Tests run in parallel. An atomic counter ensures each test gets a unique port (TCP) or socket path (Unix):

```rust
static PORT_COUNTER: AtomicU16 = AtomicU16::new(0);

fn unique_port() -> u16 {
    23600 + PORT_COUNTER.fetch_add(1, Ordering::SeqCst)
}
```

### Raw HTTP Client

Like the Python tests, integration tests build HTTP requests manually:

```rust
let request = format!(
    "POST /hook?event=PreToolUse HTTP/1.1\r\n\
     Content-Type: application/json\r\n\
     Content-Length: {}\r\n\r\n{}",
    body.len(), body
);
stream.write_all(request.as_bytes())?;
```

## Manual Testing

### Build the binary

```bash
cargo build --release
```

### TCP mode

```bash
# Start server
./target/release/rust-observatory tcp

# Send test event (in another terminal)
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"tool_name":"Bash","tool_input":{"command":"ls"}}' \
  'http://127.0.0.1:23519/hook?event=PreToolUse'

# Health check
curl -s 'http://127.0.0.1:23519/health'
```

### Unix socket mode

```bash
# Start server
./target/release/rust-observatory unix

# Send test event
curl -s --unix-socket /tmp/claude-observatory-rust.sock \
  -X POST -H 'Content-Type: application/json' \
  -d '{"tool_name":"Bash","tool_input":{"command":"ls"}}' \
  'http://localhost/hook?event=PreToolUse'

# Health check
curl -s --unix-socket /tmp/claude-observatory-rust.sock \
  'http://localhost/health'
```

### Pretty YAML output

```bash
./target/release/rust-observatory tcp --pretty-yaml
# Then send events - output will be syntax-highlighted YAML
```

## Debugging

### Verbose cargo test output

```bash
cargo test -- -v --nocapture
```

### Check binary exists

```bash
ls -la target/debug/rust-observatory
```

### Check socket file

```bash
ls -la /tmp/claude-observatory-rust.sock
# srw-rw---- 1 user group 0 Feb  9 10:00 /tmp/claude-observatory-rust.sock
```
