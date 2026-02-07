# Developer Guidelines

This document contains hook specifications quoted from official Claude Code documentation. All claims are verifiable at the source URLs.

## Official Sources

* Hooks Reference: https://code.claude.com/docs/en/hooks.md
* Hooks Guide: https://code.claude.com/docs/en/hooks-guide.md

## No-op Response Definition

> "To allow the action to proceed, omit decision from your JSON, or exit 0 without any JSON at all"
>
> Source: https://code.claude.com/docs/en/hooks.md

> "Exit 0: the action proceeds."
>
> Source: https://code.claude.com/docs/en/hooks-guide.md

**Conclusion:** Empty HTTP response body (curl outputs nothing) + exit 0 = action proceeds with no modification.

## Exit Codes

> "Exit 0 means success. Claude Code parses stdout for JSON output fields. JSON output is only processed on exit 0."
>
> Source: https://code.claude.com/docs/en/hooks.md

| Exit Code | Effect |
|-----------|--------|
| 0 | Success - action proceeds, parse JSON if present |
| 2 | Blocking error - stops the action, stderr becomes feedback |
| Other | Non-blocking error - continues, stderr shown in verbose mode |

## Hook Events Reference

### SessionStart

**When it fires:** Session begins or resumes

**Input payload:**
```json
{
  "session_id": "abc123",
  "source": "startup|resume|clear|compact",
  "model": "claude-sonnet-4-5-20250929"
}
```

**No-op response:** Exit 0 with empty/no stdout

**Note:**
> "For most events, stdout is only shown in verbose mode (Ctrl+O). The exceptions are UserPromptSubmit and SessionStart, where stdout is added as context that Claude can see and act on."
>
> Source: https://code.claude.com/docs/en/hooks.md

### UserPromptSubmit

**When it fires:** User submits a prompt (before processing)

**Input payload:**
```json
{
  "prompt": "user's prompt text"
}
```

**No-op response:** Exit 0 with empty stdout (empty string added as context = no visible effect)

### PreToolUse

**When it fires:** Before a tool executes

**Input payload (Bash example):**
```json
{
  "tool_name": "Bash",
  "tool_input": {
    "command": "npm test",
    "description": "optional description",
    "timeout": 120000
  },
  "tool_use_id": "toolu_01ABC123..."
}
```

**No-op response:** Exit 0 with empty/no JSON (default is "allow")

**Blocking response:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Reason for blocking"
  }
}
```

### PostToolUse

**When it fires:** After tool succeeds

**Input payload:**
```json
{
  "tool_name": "Edit",
  "tool_input": {"file_path": "...", "old_string": "...", "new_string": "..."},
  "tool_response": {"filePath": "...", "success": true},
  "tool_use_id": "toolu_01ABC123..."
}
```

**No-op response:** Exit 0 (informational, cannot block)

### PostToolUseFailure

**When it fires:** After tool fails

**Input payload:**
```json
{
  "tool_name": "Bash",
  "tool_input": {"command": "npm test"},
  "tool_use_id": "toolu_01ABC123...",
  "error": "Command exited with non-zero status code 1",
  "is_interrupt": false
}
```

**No-op response:** Exit 0 (informational, cannot block)

### PermissionRequest

**When it fires:** Permission dialog appears

**Input payload:**
```json
{
  "tool_name": "Bash",
  "tool_input": {"command": "..."},
  "permission_suggestions": [
    {"type": "toolAlwaysAllow", "tool": "Bash"}
  ]
}
```

**No-op response:** Exit 0 (default is "ask user")

**Auto-allow response:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
      "behavior": "allow"
    }
  }
}
```

### Notification

**When it fires:** Claude sends notification

**Input payload:**
```json
{
  "message": "Claude needs your permission to use Bash",
  "title": "Permission needed",
  "notification_type": "permission_prompt|idle_prompt|auth_success|elicitation_dialog"
}
```

**No-op response:** Exit 0 (informational only)

### Stop

**When it fires:** Main Claude finishes responding

**Input payload:**
```json
{
  "stop_hook_active": true|false
}
```

**No-op response:** Exit 0 (allows stop)

**Blocking response (keep Claude running):**
```json
{
  "decision": "block",
  "reason": "Why Claude should continue (required)"
}
```

### SubagentStart

**When it fires:** Subagent spawned

**Input payload:**
```json
{
  "agent_id": "agent-abc123",
  "agent_type": "Explore|Plan|Bash|custom-name",
  "agent_transcript_path": "~/.claude/projects/.../subagents/agent-def456.jsonl"
}
```

**No-op response:** Exit 0 (informational only)

### SubagentStop

**When it fires:** Subagent finishes

**Input payload:**
```json
{
  "agent_id": "agent-abc123",
  "agent_type": "Explore",
  "stop_hook_active": false
}
```

**No-op response:** Exit 0 (allows stop)

### PreCompact

**When it fires:** Before context compaction

**Input payload:**
```json
{
  "trigger": "manual|auto",
  "custom_instructions": "user's compact instructions (empty for auto)"
}
```

**No-op response:** Exit 0 (informational only)

### SessionEnd

**When it fires:** Session terminates

**Input payload:**
```json
{
  "reason": "clear|logout|prompt_input_exit|bypass_permissions_disabled|other"
}
```

**No-op response:** Exit 0 (informational only)

## Matchers

Events that support matchers for filtering:

| Event | Matcher filters | Example patterns |
|-------|-----------------|------------------|
| PreToolUse, PostToolUse, PostToolUseFailure, PermissionRequest | Tool name | `Bash`, `Edit\|Write`, `mcp__github__.*` |
| SessionStart | Session source | `startup`, `resume`, `clear`, `compact` |
| SessionEnd | Exit reason | `clear`, `logout` |
| Notification | Type | `permission_prompt`, `idle_prompt` |
| SubagentStart, SubagentStop | Agent type | `Explore`, `Plan`, `Bash` |
| PreCompact | Trigger | `manual`, `auto` |
| UserPromptSubmit, Stop | No matcher support | Fires on every occurrence |

## Configuration Locations

| Location | Scope |
|----------|-------|
| `~/.claude/settings.json` | All projects (user) |
| `.claude/settings.json` | Single project (shareable) |
| `.claude/settings.local.json` | Single project (gitignored) |
