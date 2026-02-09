# Testing (Unix Socket)

## Quick Start

```bash
# HTTPServer variant
uv run --script test_server.py -v

# Raw selectors variant
uv run --script test_server_selectors.py -v
```

## Test Structure

### test_server.py (28 tests)

| Class | What it tests |
|-------|--------------|
| `TestEnrichPayload` | Metadata enrichment with peer credentials |
| `TestOutputFormat` | JSONL format (single line, valid JSON, compact) |
| `TestSocketConfiguration` | Socket path precedence (CLI > env > default) |
| `TestGetTimestamp` | ISO timestamp format |
| `TestPeerCredentials` | SO_PEERCRED on real Unix socket pairs |
| `TestHookEndpoint` | HTTP endpoint for each event type via Unix socket |
| `TestHealthEndpoint` | `/health` endpoint |
| `TestOutputManager` | stdout / output socket / tee modes |

### test_server_selectors.py (15 tests)

| Class | What it tests |
|-------|--------------|
| `TestParseHttpRequest` | Manual HTTP request parsing |
| `TestBuildHttpResponse` | HTTP response construction |
| `TestEnrichPayload` | Payload enrichment (shared logic) |
| `TestSocketConfiguration` | Path precedence |
| `TestSelectorsServerIntegration` | Full server as subprocess |

## Running Specific Tests

```bash
# Single test class
uv run --script test_server.py -k TestPeerCredentials

# Single test method
uv run --script test_server.py -k test_returns_credentials_on_unix_socket

# Tests matching pattern
uv run --script test_server.py -k "returns_empty_200"
```

## How Unix Socket Tests Work

### Test Fixture

Each test class creates a temporary socket:

```python
path = tempfile.mktemp(suffix=".sock")
srv = UnixHTTPServer(path, HookHandler, 0o660, output_mgr)
```

### Raw Socket HTTP Client

stdlib `http.client` doesn't support Unix sockets, so tests build HTTP requests manually:

```python
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(socket_path)
sock.sendall(b"POST /hook?event=PreToolUse HTTP/1.1\r\n...")
response = sock.recv(4096)
```

### Subprocess Tests (selectors)

The selectors server runs as a standalone process, so integration tests launch it as a subprocess:

```python
proc = subprocess.Popen(["uv", "run", "--script", "server_selectors.py", "--socket", path])
# Wait for socket file to appear
# Send requests, verify responses
proc.terminate()
```

## Manual Testing

### Start server

```bash
./server.py --socket /tmp/test.sock
```

### Send test request

```bash
curl -s --unix-socket /tmp/test.sock \
  -X POST -H 'Content-Type: application/json' \
  -d '{"tool_name":"Bash","tool_input":{"command":"ls"}}' \
  'http://localhost/hook?event=PreToolUse'
```

### Check health

```bash
curl -s --unix-socket /tmp/test.sock 'http://localhost/health'
# {"status": "ok"}
```

### Verify peer credentials appear

Start the server and send a request. The output should include `_peer_pid`, `_peer_uid`, `_peer_gid` matching your curl process.

## Debugging

### See HTTP logs

Server logs HTTP requests to stderr:

```
[HTTP] POST /hook?event=PreToolUse HTTP/1.1 200 -
```

### Verbose pytest output

```bash
uv run --script test_server.py -v --tb=long
```

### Check socket file exists

```bash
ls -la /tmp/claude-observatory.sock
# srw-rw---- 1 user group 0 Feb  9 10:00 /tmp/claude-observatory.sock
```

The `s` at the start of permissions indicates a socket file.
