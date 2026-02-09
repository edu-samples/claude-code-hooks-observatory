# Restructure: TCP + Unix Socket Observatory Variants

Date: 2026-02-09

## Context

The repository was a single-variant educational hook observatory using HTTP/TCP. This restructuring adds a Unix socket implementation to teach IPC security concepts (SO_PEERCRED, filesystem permissions) and multi-reader output socket patterns.

## Goals

1. Restructure into `tcp-observatory/` and `unix-socket-observatory/` directories
2. Add Unix socket HTTPServer variant (familiar pattern, minimal delta from TCP)
3. Add raw selectors variant (full visibility into what HTTPServer hides)
4. Add educational documentation for both variants
5. Fix pre-existing test import bug

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Two server files in unix-socket | server.py + server_selectors.py | Teaches different abstraction levels |
| HTTPServer for server.py | Override `address_family` + `server_bind()` | Minimal delta from TCP, familiar |
| Raw selectors for server_selectors.py | `selectors.DefaultSelector()` event loop | Shows what HTTPServer hides |
| Single-threaded | `service_actions()` / selectors | Simpler than threading |
| Output socket replaces stdout | `--output-socket` replaces, `--tee` for both | Cleaner than shell-level tee |
| Socket permissions | `--mode` flag, default 0o660 | Demonstrates filesystem security |
| Peer credentials | SO_PEERCRED with graceful fallback | Linux-specific, None on other platforms |
| Test HTTP client | Raw AF_UNIX socket + manual HTTP | stdlib-only, educational |
| Hook command | `curl --unix-socket` | Widely available, consistent pattern |

## Structure

```
tcp-observatory/
├── server.py              # HTTPServer-based (moved from root)
├── install-hooks.py       # curl http://... (moved from root)
├── test_server.py         # 23 tests (moved from root)
├── configs/               # Example hook configurations
└── docs/                  # Piping examples, testing guide

unix-socket-observatory/
├── server.py              # HTTPServer + AF_UNIX override
├── server_selectors.py    # Raw sockets + selectors (no HTTPServer)
├── install-hooks.py       # curl --unix-socket
├── test_server.py         # 28 tests (HTTPServer variant)
├── test_server_selectors.py  # 15 tests (selectors + subprocess)
├── SECURITY.md            # SO_PEERCRED, permissions, comparison
├── configs/               # Unix socket hook configurations
└── docs/                  # Unix socket recipes, testing guide
```

## Implementation Steps

1. Fix test import bug (`output_jsonl` → `output_event`)
2. `git mv` files into `tcp-observatory/`
3. Create TCP educational docs (README, EDU_NOTES)
4. Update root docs (README, AGENTS.md, FUTURE_WORK.md, .gitignore)
5. Create unix-socket server.py (HTTPServer + AF_UNIX)
6. Create unix-socket server_selectors.py (raw sockets)
7. Create unix-socket install-hooks.py + configs
8. Create unix-socket tests
9. Create unix-socket documentation
10. Create this design plan document

## Platform Notes

SO_PEERCRED is Linux-specific. On macOS, the equivalent is LOCAL_PEERCRED + LOCAL_PEERPID. The implementation tries Linux first, falls back to macOS, returns None on other platforms. Tests verify credentials on Linux and gracefully handle unsupported platforms.

## Test Summary

* `tcp-observatory/test_server.py`: 23 tests (unchanged from before)
* `unix-socket-observatory/test_server.py`: 28 tests (includes peer credentials, output manager)
* `unix-socket-observatory/test_server_selectors.py`: 15 tests (HTTP parsing, subprocess integration)
* Total: 66 tests
