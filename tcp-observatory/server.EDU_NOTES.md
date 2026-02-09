# server.py - Educational Notes

How the TCP observatory server works, line by line.

## Architecture

The server is a single-file Python HTTP server built on stdlib's `http.server` module:

```
curl (hook command)  →  HTTPServer (TCP socket)  →  HookHandler.do_POST()  →  stdout (JSONL)
```

## Key Components

### HTTPServer + BaseHTTPRequestHandler

Python's `http.server.HTTPServer` is a thin wrapper around `socketserver.TCPServer`. It:

1. Creates a TCP socket and binds to `(host, port)`
2. Calls `accept()` in a loop to get client connections
3. For each connection, instantiates the handler class (our `HookHandler`)
4. The handler reads the HTTP request and calls `do_POST()` or `do_GET()`

We don't need to manage sockets, threading, or HTTP parsing - the stdlib does it all.

### HookHandler.do_POST()

The request handling flow:

```python
# 1. Parse the URL to extract ?event=PreToolUse
parsed = urlparse(self.path)
query = parse_qs(parsed.query)
event = query.get("event", ["Unknown"])[0]

# 2. Read the JSON body (Content-Length tells us how many bytes)
content_length = int(self.headers.get("Content-Length", 0))
body = self.rfile.read(content_length)
payload = json.loads(body)

# 3. Add metadata and output
enriched = enrich_payload(payload, event, client)
output_event(enriched)

# 4. Return empty 200 (no-op = action proceeds)
self.send_response(200)
```

### enrich_payload()

Adds underscore-prefixed metadata fields to distinguish our fields from Claude Code's:

* `_ts` - UTC timestamp (when we received the event)
* `_event` - event type (from URL query param)
* `_client` - client IP address

The `{**payload}` spread means original fields are preserved unchanged.

### output_event() and _output_mode

A module-level global `_output_mode` controls output format:

* `"jsonl"` (default) - compact single-line JSON, ideal for piping to `jq`
* `"pretty-json"` - indented JSON for human reading
* `"pretty-yaml"` - YAML with syntax highlighting via pygments

The `_output_mode` is set once at startup from CLI args. Using a module global keeps the handler class simple (no need to pass config through HTTPServer).

### _MultilineYamlDumper

A custom YAML dumper that renders strings containing `\n` as YAML block scalars (`|`). This makes multi-line tool responses (like file contents) readable instead of escaped on one line.

### Port Configuration Precedence

```
--port 9999          →  CLI arg wins
$CLAUDE_REST_HOOK_WATCHER  →  env var next
23518                →  default fallback
```

This three-level precedence is a common pattern. The env var allows setting the port without modifying hook commands (both server.py and install-hooks.py read it).

### Stream Separation

* **stdout**: JSONL data only - never print messages here
* **stderr**: Human-readable messages (startup banner, HTTP request logs)

This lets you pipe stdout cleanly: `./server.py | jq '.'` works because startup messages go to stderr.

## Concurrency & Parallel Requests

The server is single-threaded. `serve_forever()` uses a `selectors.PollSelector` (on Linux) that blocks until a connection is ready, handles it synchronously, then loops back to wait for the next one.

When multiple hooks fire simultaneously (e.g., from parallel subagents):

1. First connection is accepted and processed (~2ms)
2. Remaining connections wait in the kernel's listen backlog (up to 128)
3. After the first request completes, the next is accepted from the queue

No data is lost unless 129+ hooks arrive within a single request's processing time (effectively impossible). The `request_queue_size = 128` setting tells the kernel how many pending connections to hold.

Curl's `--connect-timeout 0.5 --max-time 1` ensures Claude Code never stalls more than 1 second per hook, even if the server is down or slow.

See [docs/CONCURRENCY.md](../docs/CONCURRENCY.md) for the full cross-variant analysis including backlog behavior, stdout atomicity, and SIGKILL safety.

## What HTTPServer Hides

If you want to see what's happening under the hood, check the [unix-socket-observatory/server_selectors.py](../unix-socket-observatory/server_selectors.py) which implements the same functionality using raw sockets and the `selectors` module - no HTTPServer abstraction.
