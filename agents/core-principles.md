# Core Principles

## Why?

This educational repository prioritizes clarity over cleverness. Every decision optimizes for:

1. A developer reading the code for the first time
2. Someone forking to build their own hook logic

If code requires explanation, simplify the code rather than adding comments. The goal is a codebase anyone can understand in under 10 minutes.

## Principles

### No-op by Default

The server must never break Claude Code's normal operation. Every hook response must be "no-op" - allowing actions to proceed without modification.

Official documentation states:

> "To allow the action to proceed, omit decision from your JSON, or exit 0 without any JSON at all"
>
> Source: https://code.claude.com/docs/en/hooks.md

### Source Citation Required

When discussing hook behavior, always quote official documentation with source URLs. This ensures:

* Claims are verifiable
* Developers can find authoritative details
* The codebase remains a trustworthy reference

### Security by Default

* Bind to `127.0.0.1` by default
* Never expose the server to network without explicit `--bind` flag
* Document security implications clearly

### Simplicity Over Features

* Use stdlib only for the server (no external dependencies)
* Single-file design where practical
* Prefer readable code over clever abstractions
* YAGNI - don't add features "just in case"
