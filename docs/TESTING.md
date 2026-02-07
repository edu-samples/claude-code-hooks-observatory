# Testing

## Why?

Tests demonstrate how to verify hook server behavior without running Claude Code. Each test is self-contained and readable - copy any test as a starting point for testing your custom endpoints.

## Quick Start

```bash
# Run all tests
uv run --script test_server.py

# Verbose output
uv run --script test_server.py -v
```

## Test Structure

### Test Classes

| Class | What it tests |
|-------|--------------|
| `TestEnrichPayload` | Metadata enrichment (`_ts`, `_event`, `_client`) |
| `TestOutputJSONL` | JSONL format (single line, valid JSON, compact) |
| `TestPortConfiguration` | Port precedence (CLI > env > default) |
| `TestGetTimestamp` | ISO timestamp format |
| `TestHookEndpoint` | HTTP endpoint for each event type |
| `TestHealthEndpoint` | `/health` endpoint |

### Running Specific Tests

```bash
# Single test class
uv run --script test_server.py -k TestHookEndpoint

# Single test method
uv run --script test_server.py -k test_pre_tool_use_returns_empty_200

# Tests matching pattern
uv run --script test_server.py -k "returns_empty_200"
```

## Test Coverage

Each hook event type has at least one test verifying:

1. HTTP 200 status code
2. Empty response body (no-op)

Tests for each event:

* `test_pre_tool_use_returns_empty_200`
* `test_post_tool_use_returns_empty_200`
* `test_session_start_returns_empty_200`
* `test_session_end_returns_empty_200`
* `test_user_prompt_submit_returns_empty_200`
* `test_stop_returns_empty_200`
* `test_permission_request_returns_empty_200`
* `test_notification_returns_empty_200`

## Adding Tests

When adding a new hook event type, add a corresponding test:

```python
def test_new_event_returns_empty_200(self, server: HTTPServer) -> None:
    """NewEvent: returns 200 with empty body (no-op)."""
    payload = {"field": "realistic_value"}
    status, body = self.make_request(server, "NewEvent", payload)
    assert status == 200
    assert body == b""
```

## Manual Testing

### Start server

```bash
./server.py
```

### Send test request

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"tool_name":"Bash","tool_input":{"command":"ls"}}' \
  'http://127.0.0.1:23518/hook?event=PreToolUse'
```

### Check health

```bash
curl http://127.0.0.1:23518/health
# {"status": "ok"}
```

## Debugging

### See HTTP logs

Server logs HTTP requests to stderr:

```
[HTTP] POST /hook?event=PreToolUse 200
```

### Verbose pytest output

```bash
uv run --script test_server.py -v --tb=long
```

### Print during tests

Use `capsys` fixture or temporary `print()` statements.
