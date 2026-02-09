# server_selectors.py (Raw Sockets) - Educational Notes

How the raw selectors-based server works, and what HTTPServer normally hides.

## Why This File Exists

`server.py` uses Python's `HTTPServer` which handles socket management and HTTP parsing behind clean abstractions. This file does everything manually:

* Creates sockets with `socket.socket()`
* Monitors them with `selectors.DefaultSelector()`
* Parses HTTP requests by splitting on `\r\n`
* Constructs HTTP responses byte by byte

## The Event Loop

HTTPServer's `serve_forever()` is essentially this:

```python
sel = selectors.DefaultSelector()
sel.register(input_sock, selectors.EVENT_READ)

while True:
    events = sel.select(timeout=1.0)
    for key, mask in events:
        conn, _ = input_sock.accept()
        handle_input_connection(conn)
```

`selectors.DefaultSelector()` automatically picks the best system call:

* **Linux**: `epoll` - O(1) event notification
* **macOS**: `kqueue` - BSD equivalent of epoll
* **Fallback**: `select` - works everywhere, O(n)

All three behave the same from our code's perspective.

## Manual HTTP Parsing

### Request Parsing

HTTP/1.1 requests have a simple text format:

```
POST /hook?event=PreToolUse HTTP/1.1\r\n     ← request line
Content-Type: application/json\r\n            ← headers
Content-Length: 42\r\n
\r\n                                          ← blank line separates headers from body
{"tool_name": "Bash"}                         ← body
```

Our `parse_http_request()` does:

1. Find `\r\n\r\n` (blank line) to split headers from body
2. First line → method + path
3. Remaining lines → headers dict
4. Everything after blank line → body

### Response Construction

```python
def build_http_response(status, body=""):
    return f"HTTP/1.1 {status} OK\r\nContent-Length: {len(body)}\r\n\r\n{body}"
```

That's a complete HTTP response. The protocol is simpler than it looks.

## What HTTPServer Does For You

Things `BaseHTTPRequestHandler` handles that we do manually:

| Feature | HTTPServer | Our code |
|---------|-----------|----------|
| Socket accept | `get_request()` | `sel.select()` + `input_sock.accept()` |
| HTTP parsing | `parse_request()` | `parse_http_request()` |
| Method dispatch | `do_POST()`, `do_GET()` | `if method == "POST"` |
| Response writing | `send_response()`, `end_headers()` | `build_http_response()` |
| Request logging | `log_message()` | `sys.stderr.write()` |
| Keep-alive | `handle_one_request()` loop | Close after each request |

## Single-Threaded Design

Both `server.py` and this file are single-threaded. Hook payloads are small and processing is fast, so one connection at a time is sufficient. The selectors event loop handles the output socket alongside the input socket in the same thread.

## Comparison with server.py

Read [server.EDU_NOTES.md](server.EDU_NOTES.md) for the HTTPServer approach. The two files produce identical output - the difference is only in how much of the networking is visible vs abstracted.
