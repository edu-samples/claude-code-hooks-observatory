# `--watch` Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `--watch[=INTERVAL]` to `scripts/query-hooks.py` that auto-refreshes output like `watch -n INTERVAL`, clearing the screen between iterations.

**Architecture:** Extract the body of `main()` (post-arg-parsing) into `_run_once(args)`. When `--watch` is set, loop: clear screen, print header, clear caches, re-resolve sources, call `_run_once`, sleep. Ctrl-C exits cleanly.

**Tech Stack:** Python stdlib only (argparse, time, sys, datetime). No new dependencies.

---

### Task 1: Add `--watch` argument to argparse

**Files:**

* Modify: `scripts/query-hooks.py:996-1074` (inside `parse_args()`)

**Step 1: Add the argument**

Add after the `--columns-help` argument (line ~1073), before `return parser.parse_args()`:

```python
parser.add_argument(
    "--watch",
    nargs="?",
    const=2.0,
    default=None,
    type=float,
    metavar="SECS",
    help="Auto-refresh output every SECS seconds (default: 2.0). "
    "Clears screen between refreshes, like `watch(1)`. Ctrl-C to stop.",
)
```

**Step 2: Test argument parsing manually**

Run: `uv run --script scripts/query-hooks.py --help`
Expected: `--watch` appears in help output with description

Run: `uv run --script scripts/query-hooks.py --watch=0.5 --waiting 2>&1 | head -5`
Expected: Output appears (will run once since we haven't added the loop yet)

**Step 3: Commit**

```bash
git add scripts/query-hooks.py
git commit -m "Add --watch argument to query-hooks.py argparse"
```

---

### Task 2: Extract `_run_once()` from `main()`

**Files:**

* Modify: `scripts/query-hooks.py:1160-1228` (the `main()` function)

**Step 1: Create `_run_once(args)` function**

Extract everything in `main()` after `args.columns_help` handling (line 1165) into a new function. The new `main()` calls `_run_once(args)` once:

```python
def _run_once(args: argparse.Namespace) -> None:
    """Execute a single query run (shared by normal and watch modes)."""
    global _T0

    # Validate --csv requires --waiting
    if args.csv and args.waiting is None:
        print("Error: --csv requires --waiting", file=sys.stderr)
        sys.exit(1)

    # Parse --columns
    if args.columns:
        if args.waiting is not None:
            args._columns = parse_columns(args.columns)
        else:
            args._columns = [c.strip() for c in args.columns.split(",") if c.strip()]
    else:
        args._columns = None

    sources = resolve_sources(args)

    # --waiting mode
    if args.waiting is not None:
        run_waiting(args, sources)
        return

    # Standard filter mode
    use_tail = args.last and args.last > 0
    tail: deque[dict] = deque(maxlen=args.last) if use_tail else deque()

    for line in iter_lines(sources):
        line = line.strip()
        if not line or line[0] != "{":
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not matches(event, args):
            continue
        if args._columns:
            event = {k: event[k] for k in args._columns if k in event}
        if use_tail:
            tail.append(event)
        else:
            print(format_event(event, args.jsonl))

    if use_tail:
        for event in tail:
            print(format_event(event, args.jsonl))

    if not args.no_stats:
        parts = []
        if sources:
            names = ", ".join(p.name for p in sources)
            parts.append(f"Read {names}")
        elapsed = time.monotonic() - _T0
        parts.append(f"in {elapsed:.3f}s")
        print(" | ".join(parts), file=sys.stderr)


def main() -> None:
    args = parse_args()

    if args.columns_help:
        _print_columns_help()
        return

    _run_once(args)
```

**Step 2: Verify no behavioral change**

Run: `uv run --script scripts/query-hooks.py --waiting --no-stats`
Expected: Same output as before the refactor

**Step 3: Commit**

```bash
git add scripts/query-hooks.py
git commit -m "Extract _run_once() from main() for watch mode reuse"
```

---

### Task 3: Implement the watch loop

**Files:**

* Modify: `scripts/query-hooks.py` — the `main()` function

**Step 1: Add watch loop to `main()`**

Replace `main()` with:

```python
def main() -> None:
    global _T0
    args = parse_args()

    if args.columns_help:
        _print_columns_help()
        return

    if args.watch is None:
        _run_once(args)
        return

    # --watch mode: validate interval
    interval = args.watch
    if interval <= 0:
        print(f"Error: --watch interval must be positive, got {interval}", file=sys.stderr)
        sys.exit(1)

    # Build header string (like watch(1))
    argv_str = " ".join(sys.argv[1:])
    try:
        while True:
            _T0 = time.monotonic()
            _git_root_cache.clear()
            _remote_name_cache.clear()
            # Clear screen and move cursor to top-left
            sys.stderr.write("\033[2J\033[H")
            sys.stderr.flush()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"Every {interval}s: query-hooks.py {argv_str}    {now}", file=sys.stderr)
            print(file=sys.stderr)
            _run_once(args)
            time.sleep(interval)
    except KeyboardInterrupt:
        print(file=sys.stderr)  # clean line after ^C
```

**Step 2: Bump VERSION**

Change `VERSION = "0.6.0"` to `VERSION = "0.7.0"`.

**Step 3: Test manually**

Run: `uv run --script scripts/query-hooks.py --waiting --watch=1 --no-stats`
Expected: Screen clears every 1s, header line shown, session table refreshes. Ctrl-C exits cleanly.

Run: `uv run --script scripts/query-hooks.py --waiting --watch`
Expected: Same but refreshes every 2s (default).

Run: `uv run --script scripts/query-hooks.py --watch=-1 --waiting`
Expected: Error message about positive interval, exit code 1.

**Step 4: Commit**

```bash
git add scripts/query-hooks.py
git commit -m "Add --watch mode: auto-refresh with screen clear (v0.7.0)"
```

---

### Task 4: Update docstring and examples

**Files:**

* Modify: `scripts/query-hooks.py:6-46` (module docstring)

**Step 1: Add watch examples to module docstring**

Add to the examples section:

```python
    # Live dashboard: refresh every 2 seconds (default)
    ./scripts/query-hooks.py --waiting --watch

    # Custom refresh interval
    ./scripts/query-hooks.py --waiting --watch=1.5

    # Live filter view
    ./scripts/query-hooks.py PreToolUse --watch=3
```

**Step 2: Commit**

```bash
git add scripts/query-hooks.py
git commit -m "Add --watch examples to query-hooks.py docstring"
```
