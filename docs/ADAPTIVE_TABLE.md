# Adaptive Terminal Table Design

Terminal-width-aware table rendering for `query-hooks.py --waiting`.

## Problem

Tables use fixed column widths regardless of terminal size. Narrow terminals
get truncated lines; wide terminals waste space. Resizing during `--watch`
mode has no effect.

## Design

### Column Specification

Each column has five properties:

```python
@dataclass
class ColSpec:
    priority: int      # higher = kept longer (10 = essential, 1 = nice-to-have)
    min_width: int     # below this, drop the column instead
    max_width: int     # don't stretch beyond this
    weight: float      # proportional share of flexible space
    right_align: bool = False
```

### Algorithm: Two Phases

#### Phase 1 — Column Selection (additive, highest priority first)

Start with an empty set. Add columns from highest to lowest priority. After
each addition, check if `sum(min_widths) + separators <= terminal_width`. If
adding a column would exceed, stop. Restore original column ordering for display.

```
sorted_cols = sort by priority descending
selected = []
for col in sorted_cols:
    if sum(min_widths of selected + col) + separators fits:
        selected.append(col)
    else:
        break
return in original display order
```

#### Phase 2 — Width Allocation (iterative ratio-based clamping)

Distribute available space proportionally by weight. Any column that hits its
min or max boundary gets fixed at that boundary (flexibility bit cleared).
Recalculate ratios for remaining flexible columns using remaining space. Repeat
until all columns are fixed.

```
for round in range(2 * num_columns):  # safety bound
    for each flexible column:
        raw = flex_space * (weight / total_flex_weight)
        if raw <= min: fix at min, clear flexibility
        if raw >= max: fix at max, clear flexibility
    if all settled: break
```

Convergence: each round fixes at least one column → resolves in ≤ N rounds.
The `2 * N` bound is a safety assert that should never trigger.

### Terminal Size

```python
shutil.get_terminal_size((80, 24)).columns
```

Queried once per render cycle. In `--watch` mode, naturally picks up resizes.
When piped (not a tty), falls back to 80 columns.

### Column Priority Table

| Column | Priority | Min | Max | Weight | Rationale |
|--------|----------|-----|-----|--------|-----------|
| state | 10 | 6 | 20 | 1.5 | Core — always visible |
| ago | 9 | 7 | 12 | 0.8 | Essential context |
| project | 8 | 10 | 40 | 3.0 | Identifies the project |
| tmux_target | 7 | 8 | 22 | 1.5 | Where to find the session |
| reason | 5 | 6 | 25 | 2.0 | Extra detail |
| session_id | 4 | 12 | 12 | 1.0 | Debugging only |
| cwd | 3 | 15 | 60 | 3.0 | Full path (project summarizes) |
| start_cwd | 3 | 15 | 60 | 3.0 | Full path (project summarizes) |
| tmux_session | 3 | 6 | 20 | 1.0 | tmux_target summarizes |
| tmux_window | 2 | 4 | 6 | 0.5 | tmux_target summarizes |
| tmux_pane | 2 | 4 | 5 | 0.5 | tmux_target summarizes |
| tmux_cwd | 2 | 15 | 60 | 3.0 | tmux_target summarizes |

### Integration

Only `_render_table()` changes. New functions:

* `select_columns(columns, terminal_width) -> list[str]`
* `allocate_widths(columns, terminal_width) -> dict[str, int]`

No changes to `--jsonl`, `--csv`, `--columns`, or any other code path.
Silent column dropping (no footer or warning when columns are hidden).

### Truncation

Path-like columns truncate from left: `...rvatory/scripts`.
All others truncate from right: `RUN:TaskCre...`.
Same as current behavior.
