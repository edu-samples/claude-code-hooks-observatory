# Documentation Guide

## Why?

This is an educational repository. Documentation is as important as code. Every claim about hook behavior must be verifiable against official sources.

Good documentation helps developers:

1. Understand how hooks work
2. Verify our implementation is correct
3. Build their own custom hooks

## Source Citation

### Always Quote Official Docs

When documenting hook behavior, include:

```markdown
> "Exact quote from documentation"
>
> Source: https://code.claude.com/docs/en/hooks.md
```

### Verify Before Citing

* Check the URL still works
* Confirm the quote is accurate
* Note if behavior has changed since last verification

### Official Sources

Primary sources (in order of authority):

1. https://code.claude.com/docs/en/hooks.md - Reference
2. https://code.claude.com/docs/en/hooks-guide.md - Guide

## Updating DEVELOPER_GUIDELINES.md

### When to Update

* Adding support for a new hook event
* Discovering behavior not yet documented
* Correcting outdated information

### Section Format

Each hook event follows this format:

```markdown
### EventName

**When it fires:** Brief description

**Input payload:**
> (quoted from official docs)
> Source: (URL)

**No-op response:**
> (quoted from official docs)
> Source: (URL)

**Blocking response:** (if applicable)
How to block the action, with example JSON.
```

## Updating FUTURE_WORK.md

### When to Add Items

* Deferring a feature during implementation
* Discovering an improvement opportunity
* User feedback suggesting enhancements

### Item Format

```markdown
### Title

Brief description of what and why.

Priority: High/Medium/Low
```

### Priority Guidelines

* **High**: Core functionality, blocking issues
* **Medium**: Nice-to-have improvements
* **Low**: Ideas for future consideration
