# Piping Examples (Unix Socket)

## Why?

The observatory outputs JSONL, enabling Unix-style composition. These examples show Unix socket-specific recipes including the `--output-socket` multi-reader pattern.

## Basic Usage

### Watch all events

```bash
./server.py
```

### Log to file while watching

```bash
./server.py | tee -a hooks.jsonl
```

### Silent logging (background)

```bash
./server.py >> hooks.jsonl 2>&1 &
```

## Multi-Reader Output Socket

The `--output-socket` flag creates a second Unix socket for readers. This replaces shell-level `tee` + FIFOs with a cleaner built-in mechanism.

### Start server with output socket

```bash
./server.py --output-socket /tmp/obs-out.sock
```

### Connect readers

```bash
# Reader 1: raw JSONL
socat UNIX-CONNECT:/tmp/obs-out.sock -

# Reader 2: filtered
socat UNIX-CONNECT:/tmp/obs-out.sock - | jq 'select(._event == "PreToolUse")'

# Reader 3: just tool names
socat UNIX-CONNECT:/tmp/obs-out.sock - | jq -r '.tool_name // empty'
```

### Tee mode (stdout + output socket)

```bash
./server.py --output-socket /tmp/obs-out.sock --tee
```

### Python reader (no socat needed)

```python
import socket, json

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect("/tmp/obs-out.sock")

while True:
    data = sock.recv(4096).decode()
    for line in data.strip().split("\n"):
        event = json.loads(line)
        print(f"{event['_event']}: {event.get('tool_name', '')}")
```

## Filtering with jq

### Only Bash tool events

```bash
./server.py | jq 'select(.tool_name == "Bash")'
```

### Only file-modifying tools

```bash
./server.py | jq 'select(.tool_name | IN("Write", "Edit", "NotebookEdit"))'
```

### Events from a specific PID

```bash
./server.py | jq --argjson pid $$ 'select(._peer_pid == $pid)'
```

### Events from a specific user

```bash
./server.py | jq --argjson uid $(id -u) 'select(._peer_uid == $uid)'
```

### Extract just commands being run

```bash
./server.py | jq -r 'select(._event == "PreToolUse" and .tool_name == "Bash") | .tool_input.command // empty'
```

## Manual Testing with curl

### Send a test event

```bash
curl -s --unix-socket /tmp/claude-observatory.sock \
  -X POST -H 'Content-Type: application/json' \
  -d '{"tool_name":"Bash","tool_input":{"command":"ls"}}' \
  'http://localhost/hook?event=PreToolUse'
```

### Check health

```bash
curl -s --unix-socket /tmp/claude-observatory.sock 'http://localhost/health'
```

### Send from a script (see PID in output)

```bash
echo '{"tool_name":"Read"}' | curl -s --unix-socket /tmp/claude-observatory.sock \
  -X POST -H 'Content-Type: application/json' -d @- \
  'http://localhost/hook?event=PreToolUse'
```

The server output will show `_peer_pid` matching your script's PID.

## Log Analysis

### Count events by type

```bash
cat hooks.jsonl | jq -r '._event' | sort | uniq -c | sort -rn
```

### Most active PIDs

```bash
cat hooks.jsonl | jq -r '._peer_pid | tostring' | sort | uniq -c | sort -rn
```

### Events by user

```bash
cat hooks.jsonl | jq -r '._peer_uid | tostring' | sort | uniq -c | sort -rn
```

### Session timeline

```bash
cat hooks.jsonl | jq -r 'select(._event | IN("SessionStart", "SessionEnd")) | "\(._ts) \(._event) pid=\(._peer_pid)"'
```

## Alerting

### Notify on permission requests (Linux)

```bash
./server.py | jq -r 'select(._event == "PermissionRequest") | "Permission needed: \(.tool_name)"' | while read msg; do
  notify-send "Claude Code" "$msg"
done
```
