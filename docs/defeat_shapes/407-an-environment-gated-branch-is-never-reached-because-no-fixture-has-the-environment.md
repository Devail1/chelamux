## 407. An environment-gated branch is never reached because no fixture has the environment

**Assertion form:** a function takes an extra path only when something in its *environment*
is present — a package installed in another interpreter, a binary on `PATH`, a `.venv` in the
target tree — and falls back when it is absent. The suite drives the function end to end,
the tests are real, and they go red when the fallback path breaks. But every fixture builds
the environment *without* the capability (a tmp repo has no `.venv`, the dev venv does not
ship the optional plugin), so the gated branch — including its own error handling — is dead
code as far as the suite can tell.

**Mutation that defeats it:** anything inside the gated branch. `if not baseline.green and
extra_env:` → `if False and …` (the "red only under coverage ⇒ re-run plain" recovery) stays
green across 4,570 tests, because `extra_env` is only ever non-empty when
`_has_pytest_cov(worktree)` is true, and no fixture's worktree has a `.venv` with pytest-cov
in it. The same holds for the per-sha cache, the map reaching the selector, and the detector
itself.

**Guard form that survives:** make the gate an explicit seam and drive the branch through it
— stub the capability probe (`_has_pytest_cov → True`) and replace the capability with a
stand-in that has an observable effect (coverage args → an env var one fixture test fails
under). Then assert each behaviour of the branch: the recovery re-run happened (and ran
*without* the capability), nothing was read off the run that did not produce it, the cache
was written and later reused, the result reached its consumer. Test the probe itself
separately, both ways, with a fake interpreter that does / does not have the module.

**Found:** CMX-407 round 1 (2026-09-30), PR #558 — `chela/judge.py::run_baseline`'s coverage
path. Closed by `tests/test_judge_select.py`'s `fake_cov` block
(`test_a_baseline_red_only_under_coverage_is_re_run_plain_never_CANNOT_VERIFY` and siblings).

**Related:** [65](65-an-optional-capability-s-degrade-path-is-only-exercised-by-the-implementation-that-has-it.md)
is the mirror image — there every fixture HAS the capability and the degrade path is dead;
here every fixture LACKS it and the capable path is dead. The same round's other survivor, a
kill switch hardcoded `True` at a call site, is shape [2](02-fixture-parked-on-a-default-value.md) (a fixture parked on a default).
