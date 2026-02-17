# --plugin-dir Flag, User Global Hooks, and Missing Observatory Events

Date: 2026-02-17

## Question

Do Claude Code's `--plugin-dir` flag or other CLI flags interfere with user global hooks in `~/.claude/settings.json`?

## Answer: No

`--plugin-dir` loads a plugin directory for local testing. Plugin hooks **merge additively** with user and project hooks — they never replace or bypass them.

Hook scope precedence (all layers fire independently):

| Location | Scope |
|---|---|
| `~/.claude/settings.json` | All projects (user global) |
| `.claude/settings.json` | Single project |
| `.claude/settings.local.json` | Single project, gitignored |
| Plugin `hooks/hooks.json` | When plugin enabled |

Per official docs: *"When a plugin is enabled, its hooks merge with your user and project hooks."*

The only known mechanism that suppresses user global hooks is `allowManagedHooksOnly` in enterprise managed policy settings.

## Known Bugs Where Hooks Silently Fail (unrelated to --plugin-dir)

* **Issue #11544** (v2.0.37+ regression): `~/.claude/settings.json` hooks silently not loaded. Debug logs show `Found 0 hook matchers in settings` despite valid JSON. Schema parsing regression.
* **Issue #10367** (v2.0.27): Hooks non-functional when claude launched from subdirectory — settings file path resolution breaks relative to CWD.
* **Issue #3579** (v1.0.51-52): User settings hooks not loading/displaying in `/hooks` command.
* **Issue #18547** (VSCode extension v2.1.9): Plugin `hooks/hooks.json` NOT loaded by VSCode extension.

## Debugging Hooks

```bash
# See hook execution in debug output
claude --debug

# Toggle verbose mode in-session
Ctrl+O

# Check hook matchers loaded
# Look for: [DEBUG] Found N hook matchers in settings
```

Hooks are captured at process start — mid-session external edits require `/hooks` menu review before taking effect.

## Root Cause of Missing Observatory Events

Investigation found the missing events are NOT caused by `--plugin-dir`. The actual cause is **observatory server restarts splitting session lifecycles across log epochs**:

* Sessions started before a server restart only have `SessionStart` in the current log
* Sessions started after a restart have no `SessionStart` (it was in the old log)
* The Rust observatory server was confirmed running and accepting connections

Evidence: out of 104 sessions in the log, 19 had only `SessionStart`, 31 had no `SessionStart` at all, and timestamps cluster around restart times.

## References

* [Hooks reference - Claude Code Docs](https://code.claude.com/docs/en/hooks)
* [Create plugins - Claude Code Docs](https://code.claude.com/docs/en/plugins)
* [Issue #11544: Hooks not loading from settings.json](https://github.com/anthropics/claude-code/issues/11544)
* [Issue #10367: Hooks non-functional in subdirectories](https://github.com/anthropics/claude-code/issues/10367)
* [Issue #18547: Plugin hooks not firing in VSCode](https://github.com/anthropics/claude-code/issues/18547)
* [Issue #3579: User settings hooks not loading](https://github.com/anthropics/claude-code/issues/3579)
