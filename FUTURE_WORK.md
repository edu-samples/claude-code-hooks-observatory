# Future Work

Prioritized backlog for Claude Code Hooks Observatory.

## Completed

### Unix Socket Variant

Implemented in `unix-socket-observatory/`. Teaches IPC security concepts (SO_PEERCRED, filesystem permissions) and multi-reader output socket patterns. Includes both HTTPServer-based and raw selectors-based server implementations.

### Rust Variant

Implemented in `rust-observatory/`. Single compiled binary supporting both TCP and Unix socket transports via subcommands. Teaches Rust-specific concepts: enums for transport abstraction, raw libc FFI for SO_PEERCRED, syntect for syntax highlighting, Drop trait for resource cleanup, and generic functions over stream types.

## High Priority

### Plugin Packaging

Create `plugin/` directory with proper Claude Code plugin structure:

* `plugin/plugin.json` - metadata
* `plugin/hooks/hooks.json` - hook configuration
* Documentation for symlinking to `~/.claude/plugins/`

This enables distribution as a Claude Code plugin that users can enable/disable.

### Session Differentiation

Investigate if payload contains sufficient data to distinguish which Claude Code session sent each hook event.

Check `session_id` field behavior across:

* Multiple concurrent sessions
* Session resume scenarios
* Different projects

## Medium Priority

### Docker Container Tunneling

Document and provide templates for:

* Running observatory on Docker host
* Forwarding hook requests from Claude Code inside containers
* Network namespace considerations
* Example `docker-compose.yml` with host networking

Consider edge cases:

* Isolated network namespaces
* Multiple containers running Claude Code
* Port forwarding vs host networking

### Log File Output Option

Add `--log /path/to/file.jsonl` flag to write to file in addition to stdout.

Keep stdout-only as default (Unix philosophy), but provide convenience for long-running sessions.

## Low Priority

### Log Rotation

Optional `--log-rotate` flag for long-running observatory instances.

Features to consider:

* Size-based rotation
* Time-based rotation
* Compression of rotated files

<!-- NOTE: Do NOT use Rust pipe-logger (magiclen/pipe-logger) for log rotation.
     v1.1.19 has a stdin buffer corruption bug (magiclen/pipe-logger#4) that
     duplicates/garbles piped lines. Fix submitted as magiclen/pipe-logger#5
     but not yet merged. Current approach (tee -a + shell-based rotation in
     run-with-tee-logrotator.sh) works correctly and is the recommended path.
     Revisit pipe-logger only after upstream merges the fix. -->

### Web UI Dashboard

Simple HTML dashboard showing live hook stream via WebSocket.

Features:

* Real-time event display
* Filtering by event type
* Search within payloads
* Pause/resume stream

### Event Statistics

Add `--stats` flag to print periodic statistics:

* Events per type
* Events per second
* Top tools by frequency

---

## Adding New Items

When deferring work, add here with:

1. Clear title
2. Brief description of what and why
3. Priority level (High/Medium/Low)
