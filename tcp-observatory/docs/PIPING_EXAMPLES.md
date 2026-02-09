# Piping Examples

## Why?

The observatory outputs JSONL to stdout, enabling Unix-style composition. These examples show how to filter, route, and analyze hook events in real-time.

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

### Timestamped log file

```bash
./server.py | tee -a "hooks-$(date +%Y%m%d).jsonl"
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

### Only specific event types

```bash
./server.py | jq 'select(._event | IN("PreToolUse", "PostToolUse"))'
```

### Extract just commands being run

```bash
./server.py | jq -r 'select(._event == "PreToolUse" and .tool_name == "Bash") | .tool_input.command // empty'
```

### Pretty-print events (for debugging)

```bash
./server.py | jq '.'
```

### Extract file paths from Read/Write/Edit

```bash
./server.py | jq -r 'select(.tool_input.file_path) | .tool_input.file_path'
```

## Multiple Consumers with FIFOs

FIFOs (named pipes) let multiple processes read the same stream.

### Setup

```bash
# Terminal 1: Create FIFO and start server
mkfifo /tmp/hooks-fifo
./server.py | tee /tmp/hooks-fifo
```

### Consumer Examples

```bash
# Terminal 2: Filter for errors
cat /tmp/hooks-fifo | jq 'select(._event == "PostToolUseFailure")'

# Terminal 3: Count events per type
cat /tmp/hooks-fifo | jq -r '._event' | sort | uniq -c

# Terminal 4: Watch only permission requests
cat /tmp/hooks-fifo | jq 'select(._event == "PermissionRequest")'
```

### Multiple FIFOs

```bash
# Create multiple FIFOs for different consumers
mkfifo /tmp/hooks-errors /tmp/hooks-tools /tmp/hooks-sessions

# Split to all of them
./server.py | tee /tmp/hooks-errors /tmp/hooks-tools /tmp/hooks-sessions > /dev/null &

# Each consumer reads their FIFO
cat /tmp/hooks-errors | jq 'select(._event == "PostToolUseFailure")' &
cat /tmp/hooks-tools | jq 'select(.tool_name)' &
cat /tmp/hooks-sessions | jq 'select(._event | startswith("Session"))' &
```

## Log Analysis

### Events in last 5 minutes (GNU date)

```bash
cat hooks.jsonl | jq --arg cutoff "$(date -d '5 min ago' -Iseconds)" \
  'select(._ts > $cutoff)'
```

### Count events by type

```bash
cat hooks.jsonl | jq -r '._event' | sort | uniq -c | sort -rn
```

### Most common tools

```bash
cat hooks.jsonl | jq -r '.tool_name // empty' | sort | uniq -c | sort -rn | head -10
```

### Find failed commands

```bash
cat hooks.jsonl | jq 'select(._event == "PostToolUseFailure") | {command: .tool_input.command, error: .error}'
```

### Session timeline

```bash
cat hooks.jsonl | jq -r 'select(._event | IN("SessionStart", "SessionEnd")) | "\(._ts) \(._event)"'
```

## Compact View

### One-line summaries

```bash
./server.py | jq -r '"\(._ts) \(._event) \(.tool_name // "")"'
```

### Tool calls only

```bash
./server.py | jq -r 'select(.tool_name) | "\(._event): \(.tool_name)"'
```

## Alerting

### Notify on permission requests (Linux)

```bash
./server.py | jq -r 'select(._event == "PermissionRequest") | "Permission needed: \(.tool_name)"' | while read msg; do
  notify-send "Claude Code" "$msg"
done
```

### Sound on errors (macOS)

```bash
./server.py | jq 'select(._event == "PostToolUseFailure")' | while read line; do
  afplay /System/Library/Sounds/Basso.aiff
done
```

## Cleanup

```bash
# Remove FIFOs when done
rm /tmp/hooks-fifo /tmp/hooks-errors /tmp/hooks-tools /tmp/hooks-sessions 2>/dev/null
```
