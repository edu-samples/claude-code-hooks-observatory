# Claude Code Hooks Observatory - Design Document

**Date:** 2026-02-06
**Status:** Approved

## Overview

Educational REST server for observing Claude Code hook events in real-time. Serves as both a learning tool and a starting point for custom hook implementations.

## Project Structure

```
claude-code-hooks-observatory/
├── server.py                    # Main server (uv shebang, stdlib only)
├── install-hooks.py             # Hook config installer (uv shebang)
├── test_server.py               # pytest tests
├── pyproject.toml               # Project config (pytest dependency for tests)
├── .gitignore
├── .python-version              # For uv
│
├── README.md                    # Quick start, installation, basic usage
├── DEVELOPER_GUIDELINES.md      # Official hook specs with quoted sources/URLs
├── AGENTS.md                    # AI assistant rules (references agents/*.md)
├── FUTURE_WORK.md               # Prioritized backlog
├── CLAUDE.md                    # Points to @AGENTS.md
│
├── agents/
│   ├── core-principles.md       # Always loaded - no-op principle, citations
│   ├── coding-standards.md      # Always loaded - Python style, conventions
│   ├── adding-hook-support.md   # Conditional - when adding new hook types
│   ├── testing-guide.md         # Conditional - when writing tests
│   └── documentation-guide.md   # Conditional - when updating docs
│
├── docs/
│   ├── PIPING_EXAMPLES.md       # tee, FIFOs, jq recipes
│   ├── TESTING.md               # How to run tests
│   └── plans/                   # Design documents
│
└── configs/                     # Example hook configurations
    ├── hooks-global.json        # For ~/.claude/settings.json
    ├── hooks-project.json       # For .claude/settings.json
    └── hooks-minimal.json       # Single-event example
```

## Server Design (`server.py`)

### Core Behavior

- Single-file Python using only `http.server` from stdlib
- Binds to `127.0.0.1` by default (configurable via `--bind`)
- Port precedence: CLI `--port` > env `$CLAUDE_REST_HOOK_WATCHER` > default `23518`
- Single endpoint: `POST /hook?event=<EventType>`

### Request Handling

1. Receive POST /hook?event=PreToolUse
2. Read JSON body from request
3. Enrich with metadata:
   - `_ts`: ISO timestamp
   - `_event`: from query param
   - `_client`: client IP
4. Compact to single line, print to stdout (JSONL)
5. Return HTTP 200 with empty body (no-op)

### CLI Interface

```bash
./server.py                              # Default: 127.0.0.1:23518
./server.py --port 9999                  # Custom port
./server.py --bind 0.0.0.0               # Bind to all interfaces
CLAUDE_REST_HOOK_WATCHER=8080 ./server.py  # Port from env
```

### Output

- Startup message to stderr (keeps stdout pure JSONL)
- JSONL format with `_` prefixed metadata fields
- Example: `{"_ts":"2024-01-15T10:30:00Z","_event":"PreToolUse","_client":"127.0.0.1","tool_name":"Bash",...}`

## Installer Design (`install-hooks.py`)

### CLI Interface

```bash
./install-hooks.py                    # Interactive: asks which scope
./install-hooks.py --global           # Install to ~/.claude/settings.json
./install-hooks.py --project          # Install to .claude/settings.json
./install-hooks.py --port 9999        # Custom port in generated config
./install-hooks.py --dry-run          # Show what would be written
./install-hooks.py --uninstall        # Remove observatory hooks
```

### Workflow

1. Determine target file (interactive or from flag)
2. Read existing settings (or start with {})
3. Check for existing hooks entries, ask to merge/replace/abort
4. Generate hook config for all 10 event types
5. Show unified diff of changes
6. Ask for confirmation (unless --yes)
7. Write merged settings
8. Create backup with timestamp: `settings.json.bak-YYMMDD-HHmm`

## No-op Response Verification

Based on official Claude Code documentation:

> "To allow the action to proceed, omit decision from your JSON, or exit 0 without any JSON at all"
> Source: https://code.claude.com/docs/en/hooks.md

> "Exit 0: the action proceeds."
> Source: https://code.claude.com/docs/en/hooks-guide.md

**Conclusion:** Empty HTTP response body → curl outputs nothing → exit 0 → action proceeds with no modification.

## Testing Design

- pytest with minimal fixtures
- One test per event type minimum
- Tests verify: HTTP 200 status, empty body, correct JSONL output format
- Run with: `uv run pytest`

## Agent Rules Structure

### Always Loaded (via @)

- `agents/core-principles.md` - No-op principle, source citation, security
- `agents/coding-standards.md` - Python style, type hints, conventions

### Conditionally Loaded

- `agents/adding-hook-support.md` - When adding new hook event types
- `agents/testing-guide.md` - When writing or modifying tests
- `agents/documentation-guide.md` - When updating docs

### Each File Includes "Why?"

Explains cognitive load considerations, learning curve benefits, and forking value.

## FUTURE_WORK.md Items

### High Priority

- Plugin packaging (`plugin/` directory structure)
- Session differentiation investigation

### Medium Priority

- Docker container tunneling templates and docs

### Low Priority

- Log rotation
- Web UI dashboard

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Server architecture | Single-file stdlib | Maximum readability, no dependencies |
| Output destination | stdout only | Unix philosophy, maximum flexibility |
| JSONL metadata | `_` prefixed fields | Convention, won't collide with Claude fields |
| No-op response | Empty HTTP 200 | Verified against official docs |
| Port config | CLI > env > default | Standard precedence pattern |
| Bind address | 127.0.0.1 default | Security by default |
| Backup format | `.bak-YYMMDD-HHmm` | Preserves history |
| Tests | pytest | Standard, readable, uv handles dependency |
