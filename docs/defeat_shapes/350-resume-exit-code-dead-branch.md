## 350. A branch that changes an outcome (not just a value) is only ever exercised on the side where the outcome already matches the fallback

**Assertion form:** `chela restore`'s exit-code rule has one designed exception —
`--resume is the one exception` (`chela/main.py`): a MANUAL row `--resume` actually
`RESUMED` is no longer orphaned, so the exit code should ignore it, while an unresolved
MANUAL row (still `skipped`/`resume-failed`, or `--resume` not even attempted) must still
force exit 1. The code is an `if resume_flag and results: still_orphaned = any(...)  else:
still_orphaned = bool(manual)`. Every existing `--resume` test that checked the exit code
(`test_chela_restore_resume_relaunches_the_MANUAL_session_ids_row` and its neighbors) drove a
batch where at least one MANUAL row was *not* resolved by resume (a `telegram.bindings` row
"left to daemon forever", or a guard-refused row) — so `still_orphaned` came out `True` on
both sides of the branch: the real `any(...)` computation AND the `bool(manual)` fallback it
guards against. No fixture ever put a `--resume` call in the one state where the two
computations disagree — every MANUAL row in the batch actually resumed.

**Mutation that defeats it:** `if resume_flag and results:` → `if False and resume_flag and
results:`. The `any(...)` computation is now dead code; every `--resume` call falls through
to `still_orphaned = bool(manual)`, which is `True` any time the batch contains a MANUAL
verdict at all — resumed or not. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
under this mutation because no test's fixture ever placed all of a batch's MANUAL rows into
`resumed`, so `bool(manual)` and the real rule always happened to agree on every fixture in
the suite.

**Guard form that survives:** build a fixture on the disagreement point itself, not just on
"this feature runs at all". Here that means a case where `resume()` reports `RESUMED` for
*every* MANUAL verdict in the batch — `still_orphaned` must be `False` (exit 0) under the
real rule but `True` (exit 1) under `bool(manual)` — paired with the existing counterweight
where a row comes back `skipped`/`resume-failed` (both rules agree: exit 1). A conditional
that changes an *outcome* rather than a *value* needs a fixture that visits the one state
where its two arms would print or return differently; a suite that only ever exercises the
side both arms already agree on can stay green forever regardless of which arm is live.

**Found:** CMX-350 rework round 1 (2026-09-09), PR #461. The judge's required-mutation-set
verdict named `chela/main.py`'s `--resume is the one exception` branch: `if resume_flag and
results:` deleted to `if False and resume_flag and results:`, full suite (3784 tests) still
green. Fixed by adding
`test_restore_resume_exits_ZERO_when_the_only_MANUAL_row_was_actually_RESUMED` (drives
`plan`/`resume` stubs so every MANUAL verdict comes back `RESUMED` — must exit 0) alongside
`test_restore_resume_exits_NONZERO_when_a_MANUAL_row_was_not_resolved_by_resume` (the
counterweight — a `SKIPPED` result must still force exit 1) in `tests/test_restore_cli.py`.
