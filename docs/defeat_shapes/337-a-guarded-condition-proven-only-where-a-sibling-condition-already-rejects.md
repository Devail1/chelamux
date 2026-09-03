## 337. A guarded condition proven only where a sibling condition already rejects

**Assertion form:** a match is gated by two independent conditions ANDed together — "at
column 0" and "carries the active-turn marker" — and the one fixture exercising rows past
the first happens to fail *both* conditions at once (an indented bullet with no ellipsis).
The test asserts the whole row is rejected, which reads as pinning column-0, but the
ellipsis gate alone would already reject that exact input — the fixture never varies the
two conditions independently, so nothing in the suite tells you which gate is doing the
rejecting.

**Mutation that defeats it:** relax the untested gate (drop column-0 for every row past the
first: `line[0]` → `line.strip()[0]` for non-first rows) while leaving the tested gate
(ellipsis) untouched. The existing negative-control fixture still rejects, because its
bullets carry no `…` and the ellipsis gate alone still rejects them — so the suite stays
green even though column-0 is now unenforced for every row the widened scan reaches.

**Why the existing negative control doesn't catch it:** a fixture built at the intersection
of "fails condition A" and "fails condition B" only proves the disjunction (A-or-B rejects
it); it says nothing about whether A is load-bearing on its own. Distinct from shape 66 (a
two-axis predicate where the untested axis is simply never exercised at all): here BOTH
axes are exercised by the same fixture, but always in the *same* combined-reject direction,
so the two negative results collapse onto one line and neither is separable from the other.

**Guard form that survives:** for a two-gate AND, add a fixture that satisfies the gate
you're not focused on while deliberately failing only the one you want pinned — an indented
bullet that DOES carry `…`, past the first row, isolates column-0 from the ellipsis check
and forces column-0 alone to be the reason the match is rejected.

**Found:** `chela.telegram.panescan.detect_status` (CMX-337, PR #434, round 1). The judge
mutated `if line[0] in STATUS_SPINNERS:` to `if (line[0] if is_first else
line.strip()[0]) in STATUS_SPINNERS:` — dropping the column-0 requirement for every row
past the first non-blank one. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
(3515 passed) because `BULLETS_PANE`'s indented bullets ("· rotate the group", "· third
bullet") carry no `…`, so the ellipsis gate alone already rejected them at every row past
the first — the same rejection the column-0 gate would have produced, so widening the
latter changed nothing observable. Closed by
`test_an_indented_ellipsis_bullet_past_the_first_row_is_not_a_status_line`, which adds
`INDENTED_ELLIPSIS_PANE`: an indented bullet carrying `…`, sitting behind a banner row (not
the first non-blank row above the chrome) — under the mutation this is read as an active
status line (`Status(verb='· deploying the thing…', active=True)`); the correct code
returns `None`.

**See also:** [[66|shape 66]] — the same family (an AND of independently-variable
conditions), but there the untested axis is never exercised at all rather than exercised
redundantly through the tested axis's own rejection.

**Recurred:** CMX-337 rework round 2, same PR #434. The round-1 fix closed column-0 at
exactly ONE position — `INDENTED_ELLIPSIS_PANE`'s ellipsis-carrying indented bullet sits at
`chrome_idx-2` — while the production comment it justified claims column-0 holds "at any
distance". The widened scan reaches `chrome_idx-1` through `chrome_idx-4`
(`_STATUS_LOOKBACK == 4`), so two of those four reachable rows (`chrome_idx-3`,
`chrome_idx-4`) had no column-0 coverage at all. The judge's round-2 mutation —
`if line[0] in STATUS_SPINNERS:` → `if (line[0] if i >= chrome_idx - 2 else
line.strip()[0]) in STATUS_SPINNERS:` — enforces column-0 only within two rows of the chrome
and relaxes it deeper, exactly the two rows `INDENTED_ELLIPSIS_PANE` never reached.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3516 passed) under it. This is the
same shape as the round-1 entry above (a condition proven only where it doesn't have to do
the work) but the axis that goes unexercised is POSITION within a bounded scan range, not a
sibling boolean condition on the same row — and unlike shape 53's unbounded family of
position formulas, `_STATUS_LOOKBACK` is a small, fixed constant, so the range is exhaustively
enumerable: pinning both ends closes it completely, no differential fixture needed. Closed by
two more fixtures, `INDENTED_ELLIPSIS_DEEPER_PANE` (bullet at `chrome_idx-3`) and
`INDENTED_ELLIPSIS_DEEPEST_PANE` (bullet at `chrome_idx-4`, the farthest row the lookback
reaches) — under the mutation both are read as an active status line
(`Status(verb='· deploying the thing…', active=True)`); the correct code returns `None` for
both. **Guard form that survives, updated:** when a claim like "at any distance" is backed by
a BOUNDED scan, don't stop at one interior fixture — pin the near end (first row past the
unconditional-accept row) AND the far end (the last row the lookback constant actually
reaches); a single interior position leaves either side of it free for a mutation to relax.
