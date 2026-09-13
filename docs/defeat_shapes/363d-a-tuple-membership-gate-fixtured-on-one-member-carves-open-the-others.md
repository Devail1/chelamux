## 363d. A single boolean gate gets a fixture for one member of the tuple it's guarding, leaving room for a carve-out on the rest

**Assertion form:** `chela/dispatcher.py`'s reconcile-to-`done` gate reads
`not _is_adopted(row) and not tracker_read_failed and row["status"] in REVIEW_STATUSES`, where
`REVIEW_STATUSES = ("awaiting_review", "changes_requested", "needs_human")` — one shared
boolean protecting all three statuses identically. The only test driving this branch on a
failed tracker read
(`test_tick_does_not_reconcile_a_review_row_when_the_tracker_read_fails`) seeded exactly one
status, `awaiting_review`, even though its own docstring claimed the guard covers "every
awaiting_review/changes_requested/needs_human row." A single fixture against a multi-member
tuple reads as full coverage of the condition — the `in` test *looks* atomic — but nothing
proved the other two members ever reach the same gate.

**Mutation that defeats it:** `chela/dispatcher.py`, the gate's `and` chain —
```
- and not tracker_read_failed and row["status"] in REVIEW_STATUSES):
+ and (not tracker_read_failed or row["status"] != "awaiting_review") and row["status"] in REVIEW_STATUSES):
```
This carves an exact exception for the one status the fixture happens to seed:
`awaiting_review` keeps its original protection (the mutated clause still requires
`not tracker_read_failed` for it, so the seeded fixture's assertion is undisturbed), while
`changes_requested` and `needs_human` rows now reconcile to `done` on a failed-read tick
regardless of `tracker_read_failed` — the exact bug this PR exists to close, just scoped to
two-thirds of the statuses it claims to protect. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
stayed green (3974 passed / 0 failed) with the corruption in place, because no fixture ever
seeded a `changes_requested` or `needs_human` row on a failed-read tick.

**Why this is a distinct shape from [[363c|363c]]:** 363c is about several *separate* branches
in `gh_issues.py`, each assigning the flag independently, where sampling stops at "some branch
has a test." This shape is narrower and easier to miss: it is a *single* line of code, a
*single* boolean expression, gating a tuple-membership check — the kind of condition a reader
skims past as "one thing," because syntactically it is. The mutation doesn't touch a branch the
suite never visits at all; it inserts a second, narrower condition that only diverges from the
original on inputs the suite never varies (the other two tuple members). Anywhere a guard reads
`x in SOME_TUPLE` with `len(SOME_TUPLE) > 1`, one fixture proves the guard fires for that one
member — never that it fires identically for the rest.

**Guard form that survives:** parametrize the fixture over every member of the tuple, not just
one representative — `@pytest.mark.parametrize("seeded_status", dispatcher.REVIEW_STATUSES)` —
seeding the row's status from the parameter and asserting the row is left untouched
(`reconciled_done == 0`, final status unchanged) for each. A carve-out scoped to any single
status then fails on the other two parametrized cases, whichever status the carve-out happens
to spare.

**Found:** CMX-363 rework round 4 (2026-09-13), PR #498 — judge's required-mutation-set
verdict; the mutation SURVIVED the round-3 suite (`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`,
3974 passed / 0 failed both before and with the mutation applied). Closed by parametrizing
`test_tick_does_not_reconcile_a_review_row_when_the_tracker_read_fails` over
`dispatcher.REVIEW_STATUSES` (`tests/test_dispatcher_worktree_gc.py`). The same round's other
mutation — `chela/sources/markdown.py` downgrading its failed-read log from `log.warning` to
`log.debug` with zero test ever asserting the level — is a recurrence of [[311|shape 311]] (a
negative/positive control written for one sibling implementation, `gh_issues.py`'s
`test_unconfigured_refuses_and_SAYS_SO`, never mirrored onto the structurally identical
second); closed by adding `test_a_failed_markdown_tracker_read_logs_at_WARNING`, not by a new
shape entry.
