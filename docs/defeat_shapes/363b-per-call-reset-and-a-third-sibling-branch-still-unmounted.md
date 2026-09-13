## 363b. A flag's per-call reset, and a third sibling failure branch, still unmounted after the first rework

**Assertion form:** round 1's rework (shape [[363|363]]) added `.read_failed is True`
assertions on the non-zero-exit and config_error branches of
`GhIssuesSource.list_open_tasks()`, plus the `False` case on a tick that never touched
`gh_issues` at all (`MarkdownSource`). That closed the two branches the round-1 judge
happened to sample, but left two more assignments to the same flag completely unmounted:
the per-call reset at the top of `list_open_tasks()` (`self.read_failed = False`, run
before every attempt) and the `FileNotFoundError` / `subprocess.TimeoutExpired` branch
(`gh` missing or hanging). Fixing the branches a verdict names, instead of enumerating
every assignment to the flag the PR introduces, leaves the untouched ones exactly as
unguarded as before the rework.

**Mutation that defeats it:** two independent one-line mutations, both invisible to the
round-1 suite:
1. `self.read_failed = False` → `self.read_failed = True` at the top of
   `list_open_tasks()`. No test ever calls the method twice, or presets `read_failed` on
   an instance before a successful call, so nothing observes whether the reset actually
   runs — a hardcoded `True` there passes every existing fixture because they only ever
   check the flag after a call that started from a fresh instance's own `False` default.
2. The `except (FileNotFoundError, subprocess.TimeoutExpired)` branch's
   `self.read_failed = True` → `self.read_failed = False`. Round 1's own non-blocking
   notes named this branch explicitly as one of "three failure branches this PR added the
   flag to but mounted no test for" — and it survived into round 2 anyway.

**Guard form that survives:**
- Preset `src.read_failed = True` by hand, drive a *successful* fake `gh` call, and assert
  `src.read_failed is False` afterwards (`test_a_successful_read_resets_read_failed_to_false`)
  — presetting the opposite value first means the assertion can only pass if the reset
  itself ran, not because a fresh instance already defaulted to `False`.
- Monkeypatch `subprocess.run` to raise `FileNotFoundError` and assert
  `src.read_failed is True` (`test_gh_missing_or_timing_out_sets_read_failed`).

**Found:** CMX-363 rework round 2 (2026-09-13), PR #498 — judge's required-mutation-set
verdict; both mutations SURVIVED the round-1 suite (`CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q`, 3967 passed / 0 failed both before and with each mutation applied). Same root
shape as [[363|363]]: a multi-branch flag gets tests for whichever branches a single
review pass happened to sample, not for the full set of assignments the PR actually
touches. See also [[311|shape 311]] (a control written for one sibling implementation
never mirrored onto a structurally identical second) — the judge's own non-blocking notes
on this PR flagged three of gh_issues' five `read_failed = True` assignments as still
unmounted even after this fix; the remaining two (`repo unresolvable`, `bad JSON`) are
left as the sampled-class case per that shape, not separately mounted here.
