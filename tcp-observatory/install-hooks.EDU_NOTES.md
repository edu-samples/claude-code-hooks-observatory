# install-hooks.py - Educational Notes

How the hook installer works and what each curl flag does.

## What It Does

Generates and merges Claude Code hook configuration into your settings files. Each hook event gets a curl command that forwards the event payload to the observatory server.

## The curl Command

```bash
curl -s --connect-timeout 1 --max-time 2 \
  -X POST -H 'Content-Type: application/json' -d @- \
  'http://127.0.0.1:23518/hook?event=PreToolUse' || true
```

### Flag Breakdown

| Flag | Purpose |
|------|---------|
| `-s` | Silent mode - no progress bar or error messages |
| `--connect-timeout 1` | Give up connecting after 1 second (localhost is instant) |
| `--max-time 2` | Total operation timeout of 2 seconds (keeps hooks snappy) |
| `-X POST` | HTTP POST method |
| `-H 'Content-Type: application/json'` | Tell server we're sending JSON |
| `-d @-` | Read request body from stdin (Claude Code pipes the payload) |
| `\|\| true` | If curl fails (server not running), exit 0 anyway |

### Why `|| true`?

Without `|| true`, when the observatory server isn't running, curl exits with code 7 (connection refused). Claude Code treats non-zero hook exit as an error and shows a warning on every tool use. `|| true` makes the hook silently no-op.

### Why `-d @-`?

Claude Code writes the hook payload as JSON to the hook command's stdin. The `-d @-` flag tells curl to read the POST body from stdin, forwarding the payload directly.

## Settings Merge Logic

The installer handles three scenarios:

1. **No existing hooks** - writes the full observatory config
2. **Existing hooks, merge mode** - adds observatory hooks alongside existing ones
3. **Existing hooks, replace mode** - overwrites all hooks with observatory config

Merge operates per-event: if you have custom `PreToolUse` hooks, replacing `PreToolUse` preserves your `PostToolUse` hooks.

## Backup Strategy

Before writing any changes:

1. Creates a timestamped copy: `settings.json.bak-260206-1430`
2. Shows a unified diff of changes
3. Asks for confirmation (unless `--yes`)

## Uninstall Logic

The `--uninstall` flag removes hooks by matching on the observatory URL marker (`http://127.0.0.1:23518/hook`). It preserves any non-observatory hooks in the same event.

## Configuration Locations

| Flag | Path | Scope |
|------|------|-------|
| `--global` | `~/.claude/settings.json` | All projects |
| `--project` | `.claude/settings.json` | Current project |

Without a flag, the installer prompts interactively.
