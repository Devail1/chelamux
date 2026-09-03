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

**Recurred: CMX-337 rework round 3, same PR #434 — twice, on both gates of the pair.**

1. The round-2 fix (above) pinned the column-0 gate at both ends of the lookback but left
   the *sibling* gate — the ellipsis check — pinned only at the far end
   (`chrome_idx-4`, via `test_a_stale_spinner_line_further_up_the_pane_is_ignored` and
   `test_a_settled_line_found_only_by_scanning_past_a_banner_is_rejected`, which both
   happen to put their settled line at the same depth). The judge's round-3 mutation —
   `if is_first or _STATUS_ACTIVE in candidate:` → `if is_first or i > chrome_idx - 4 or
   _STATUS_ACTIVE in candidate:` — enforces the ellipsis gate only at `chrome_idx-4` and
   accepts a settled column-0 line unconditionally at every nearer row (`chrome_idx-1`
   through `chrome_idx-3`). `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
   (3518 passed) under it. Closed by two more fixtures at the previously-uncovered near
   rows — `SETTLED_BEHIND_BANNER_NEAR_PANE` (`chrome_idx-2`) and
   `SETTLED_BEHIND_TWO_BANNERS_PANE` (`chrome_idx-3`) — exercised by
   `test_a_settled_line_two_rows_back_is_rejected` and
   `test_a_settled_line_three_rows_back_is_rejected`; under the mutation both resolve to
   `Status(verb='Worked for 1m 17s', active=False, ...)` instead of `None`. **The
   "pin both ends" rule from round 2 was applied to only one of the two ANDed gates —
   this pair's two conditions have to be closed independently, at both ends, each.**

2. Separately, a NEW sub-shape on the *same* column-0 gate: `BULLETS_PANE`'s own comment
   claims its indented bullet is "the very last body line — directly above the chrome",
   but the fixture has a blank spacer between the last bullet and the chrome rule, so the
   bullet it actually exercises sits at `chrome_idx-2`, not `chrome_idx-1`. No fixture in
   the file ever put an indented bullet at `chrome_idx-1` — the row that takes the
   `is_first` path (active-or-settled accepted unconditionally once column-0 passes),
   making it the single position where a relaxed column-0 check costs the most. The
   judge's mutation — `if line[0] in STATUS_SPINNERS:` → `if (line.strip()[0] if i ==
   chrome_idx - 1 else line[0]) in STATUS_SPINNERS:` — relaxes column-0 at exactly that
   untested row and left it enforced everywhere else the round-2 fixtures already covered.
   Suite stayed green (3518 passed). Closed by `BULLET_DIRECTLY_ABOVE_CHROME_PANE` (the
   `BULLETS_PANE` shape with the spacer removed, so the last bullet is truly adjacent to
   the chrome) via `test_a_bullet_directly_above_the_chrome_is_not_a_status_line`; under
   the mutation it resolves to `Status(verb='· third bullet', active=False)` instead of
   `None`. **A comment's claimed position is not evidence of the fixture's actual
   position — when a docstring/comment says "this row" about a hand-authored multi-line
   fixture, recompute the index rather than trusting the prose; a spacer or an extra line
   silently shifts everything below it.**

**Recurred: CMX-337 rework round 4, same PR #434 — the "pin both ends" rule from round 2
applied to the REJECTION gates only; the two ACCEPTANCE paths those gates guard were never
given the same treatment.**

Rounds 2-3 closed every gate that *rejects* a bad row at every reachable depth
(`chrome_idx-1` through `chrome_idx-4`): column-0 for indented bullets, and the ellipsis
check for settled lines found behind a banner. But the whole point of widening the scan was
to *accept* good rows behind those same banners, and both acceptance paths were proven at
exactly one depth each — the *opposite* half of shape 337's own lesson ("pin the near end
and the far end") had only ever been applied to the negative direction:

1. `if is_first or _STATUS_ACTIVE in candidate:` — "found behind a tip block and/or an
   update banner" was proven only by `TIP_UPDATE_BANNER_PANE`, whose status sits at
   `chrome_idx-4` (a two-line tip block). The judge's mutation —
   `if is_first or (i == chrome_idx - 4 and _STATUS_ACTIVE in candidate):` — restricts
   scan-past acceptance to that single depth and rejects everywhere else the scan reaches.
   `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3551 passed) because no
   fixture ever asked "is the status still found" at `chrome_idx-2` or `chrome_idx-3` — a
   one-line tip block, or the update banner alone, are the *majority* real-world case
   (shorter rendered text), not an edge case, and this mutation makes the feature silently
   stop firing at exactly those depths — issue #432 again, one or two rows nearer the
   chrome. Closed by `TIP_UPDATE_ONE_LINE_TIP_PANE` (`chrome_idx-3`) and
   `UPDATE_BANNER_ONLY_PANE` (`chrome_idx-2`), both asserting `detect_status(...)` is NOT
   `None`; under the mutation both resolve to `None` instead of the expected `Status`.

2. `is_first = not seen_first_nonblank` — the production comment's claim that "the FIRST
   non-blank row is still accepted unconditionally (active or settled)" was proven only at
   `chrome_idx-2` (every is_first fixture — `WORKING_PANE`, `SETTLED_SHELLS_PANE`,
   `SETTLED_QUIET_PANE` — puts its status behind a blank spacer). No fixture put a status
   line at `chrome_idx-1` — the round-3 fixture that finally reached that row
   (`BULLET_DIRECTLY_ABOVE_CHROME_PANE`) only exercises the REJECTION direction (an
   indented bullet that must stay rejected there), leaving the ACCEPTANCE direction at
   that exact position untested. The judge's mutation — `is_first = not
   seen_first_nonblank and i == chrome_idx - 2` — restricts the unconditional accept to
   that one depth, so a settled summary with no spacer directly above the chrome resolves
   to `None`, losing the "shell still running" warning and the turn receipt. Suite stayed
   green (3551 passed). Closed by `SETTLED_DIRECTLY_ABOVE_CHROME_PANE` (the
   `SETTLED_SHELLS_PANE` shape with the spacer removed) via
   `test_a_settled_status_directly_above_the_chrome_is_found`, asserting the status IS
   found; under the mutation it resolves to `None` instead.

**Guard form that survives, updated again:** "pin the near end and the far end" applies to
BOTH directions of every gate inside a bounded scan, not just the direction a rejection
fixture happens to test. A rejection fixture proves a bad input stays out; only a positive
fixture at the same depths proves a good input still gets in — and on this guard the
acceptance failure mode is the silent one (`detect_status` returning `None` looks exactly
like an idle pane), so it is the direction most worth pinning first, not last.

**Recurred: CMX-337 rework round 5, same PR #434 — a distinct sub-shape, same family
("a guarded condition proven where something else is already doing the work"), found on the
OTHER half of this PR's diff, `.gitignore`, not `panescan.py`.**

Round 4's fix (and every non-blocking note through round 4) left `.gitignore`'s new
`*selfcheck*.json` pattern — added earlier on this branch for the no-separator spelling of a
committed self-check scratch artifact — with no probe of its own in
`tests/test_gitignore_scratch_files.py::REAL_SELF_CHECK_SCRATCH_FILENAMES`. That list's own
comment says to keep it "the full list, not a representative sample," and every filename
already in it is matched by one of the OTHER four patterns
(`.chela-self-check-*.json`, `*self[-_]check*.json`, `*scratch*experiment*.json`) — so the new
pattern was never the reason any assertion passed. The judge's round-5 mutation —
`*selfcheck*.json` → `*selfcheckDISABLED*.json` — kept a syntactically valid, matches-nothing
gitignore glob, and `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3554 passed).
This is the same underlying failure as the rest of this entry (a condition proven only where
it doesn't have to do the work — here, four *other* patterns "do the work" of matching every
existing probe) but on a hand-maintained pattern-list-vs-probe-list pair rather than a
position-gated boolean inside a scan loop: adding a line to one list does not require, or
even hint at, adding the matching line to the other. Closed by adding
`.chela_selfcheck_cmx337_round3.json` — the real filename that motivated the pattern, and the
only probe in the list `*selfcheck*.json` alone matches — to
`REAL_SELF_CHECK_SCRATCH_FILENAMES`; under the mutation
`test_gitignore_matches_a_real_self_check_scratch_filename[.chela_selfcheck_cmx337_round3.json]`
fails (`git check-ignore` exit 1) instead of passing. **Guard form that survives, extended to
list-of-patterns guards:** when a change adds a new entry to one side of a hand-maintained
pattern-list/probe-list pair, the same change must add a probe on the other side that ONLY
the new entry matches — not one already covered by an existing entry — or the new entry is
dead weight from the commit that adds it, with no test signal to say so.

**See also:** [[26|shape 26]] — the same underlying failure (a claim with zero guard of its
own, hiding behind an otherwise-large green suite), but shape 26 is about a *runtime literal*
a PR's prose claims to have changed; round 5 above is one line in an already
enumeration-tested list that was simply never added to its own parallel enumeration.
