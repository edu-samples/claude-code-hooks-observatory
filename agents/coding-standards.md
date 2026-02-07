# Coding Standards

## Why?

A developer should understand the entire server in under 10 minutes. Single-file design means no jumping between modules. Type hints serve as inline documentation. Match statements make event routing scannable.

This is an educational repository - code readability is the primary feature.

## Standards

### Python Version

* Python 3.10+ required
* Use `match` statement for event routing (clear, scannable)
* Use union types (`str | None`) over `Optional[str]`

### Type Hints

* All function signatures must have type hints
* Use `from __future__ import annotations` for forward references
* Type hints are documentation - make them clear

### File Structure

* Single-file for main components (`server.py`, `install-hooks.py`)
* No deep module hierarchies
* If you need to import from another file, reconsider the split

### Output Streams

* **stdout**: JSONL data only (for piping)
* **stderr**: Human-readable messages (startup, errors, info)
* Never mix data and messages on the same stream

### uv Shebang

All executable Python files use:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]  # if needed
# ///
```

### Docstrings

* Module-level docstring explaining purpose and usage
* Function docstrings for non-obvious behavior
* Keep them brief - the code should be self-explanatory

### Error Handling

* Fail fast with clear error messages
* Write errors to stderr
* Use appropriate exit codes
