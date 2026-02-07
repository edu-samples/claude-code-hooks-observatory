# Adding Hook Support

## Why?

When adding support for a new hook event type, consistency matters. Each hook must be documented with official sources, tested, and configured correctly. This guide ensures all additions follow the same pattern.

## Checklist

When adding a new hook event type:

### 1. Research Official Documentation

* Find the event in https://code.claude.com/docs/en/hooks.md
* Note the payload structure (what fields are received)
* Note what response makes it "no-op"
* Copy exact quotes with source URLs

### 2. Update DEVELOPER_GUIDELINES.md

Add a section for the new event:

```markdown
### EventName

**Input payload:**
> (quoted from official docs)
> Source: https://code.claude.com/docs/en/hooks.md

**No-op response:**
> (quoted explanation)
> Source: (URL)
```

### 3. Add Test Case

In `test_server.py`, add to `TestHookEndpoint`:

```python
def test_event_name_returns_empty_200(self, server: HTTPServer) -> None:
    """EventName: returns 200 with empty body (no-op)."""
    payload = {"field": "value"}  # realistic payload
    status, body = self.make_request(server, "EventName", payload)
    assert status == 200
    assert body == b""
```

### 4. Update Config Files

Add the event to all files in `configs/`:

* `hooks-global.json`
* `hooks-project.json`
* `hooks-minimal.json` (if appropriate)

### 5. Update install-hooks.py

Add the event to:

* `HOOK_EVENTS` list
* `MATCHER_EVENTS` dict (if it uses a matcher)

### 6. Run Tests

```bash
uv run --script test_server.py
```

All tests must pass before committing.
