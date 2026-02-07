# Testing Guide

## Why?

Tests demonstrate how to verify hook server behavior without running Claude Code. Each test is self-contained and readable - copy any test as a starting point for testing your custom endpoints.

Tests also serve as documentation, showing exactly what behavior the server guarantees.

## Test Structure

### Test Classes

* `TestEnrichPayload` - Metadata enrichment logic
* `TestOutputJSONL` - JSONL output format
* `TestPortConfiguration` - Port precedence logic
* `TestGetTimestamp` - Timestamp format
* `TestHookEndpoint` - HTTP endpoint behavior per event
* `TestHealthEndpoint` - Health check endpoint

### One Test Per Event Type

Every hook event type must have at least one test in `TestHookEndpoint`:

```python
def test_event_name_returns_empty_200(self, server: HTTPServer) -> None:
    """EventName: returns 200 with empty body (no-op)."""
    payload = {"realistic": "payload"}
    status, body = self.make_request(server, "EventName", payload)
    assert status == 200
    assert body == b""
```

## Running Tests

```bash
# All tests
uv run --script test_server.py

# Verbose output
uv run --script test_server.py -v

# Specific test class
uv run --script test_server.py -k TestHookEndpoint

# Single test
uv run --script test_server.py -k test_pre_tool_use_returns_empty_200
```

## Writing New Tests

### Use Realistic Payloads

Reference DEVELOPER_GUIDELINES.md for actual payload structures from official docs.

### Test the Contract

Focus on testing the public contract:

* HTTP status code
* Response body
* JSONL output format

Don't test internal implementation details.

### Keep Tests Independent

Each test should:

* Create its own server fixture
* Not depend on other tests running first
* Clean up after itself

### Mock External Dependencies

Use `unittest.mock` for:

* stdout capture (`io.StringIO`)
* Environment variables (`patch.dict(os.environ)`)
* stderr suppression in error tests
