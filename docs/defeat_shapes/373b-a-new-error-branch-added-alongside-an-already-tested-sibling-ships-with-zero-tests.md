## 373b. A new error branch added alongside an already-tested sibling branch ships with zero tests of its own

**Assertion form:** the watchdog's stuck-agent re-nudge path in `chela/dispatcher.py`'s `tick`
has two branches on `TemplateRenderError` from `_renudge_prompt`: a first-dispatch row goes to
`status='failed'` via a plain `UPDATE`, and a rework row goes to `changes_requested` via
`_rework_failed(conn, row, f"rework re-nudge: {e}")` — deliberately different, because `failed`
is the fresh-dispatch retry state and would let a dead rework be re-claimed as a brand-new task,
losing its verdict and bypassing the rework cap (see `_rework_failed`'s own docstring). The
first-dispatch half already had a test elsewhere in the render_prompt suite pinning that a bad
template fails only the one run. The rework half — the more dangerous branch, since getting it
wrong reintroduces the exact bug `_rework_failed` exists to prevent — had **no test at all**.

**Why that's a gap and not just missing coverage:** the judge's mutation replaced the
`_rework_failed(...)` call with `pass`. Nothing in the suite calls `tick` with a `running` rework
row, a live tmux window reading idle-at-empty-prompt, and a `REWORK_PROMPT` that references an
unknown variable — so nothing ever reaches this line under test, and a no-op in its place is
indistinguishable from the real call to every existing assertion. A sibling branch being tested
creates a false sense that "this area is covered" when the new branch added right next to it
isn't.

**Mutation that defeats it:** replace `_rework_failed(conn, row, f"rework re-nudge: {e}")` with
`pass` at the site inside `tick`'s watchdog re-nudge exception handler, guarded by `_is_rework(row)`.
The full suite stays green because no test exercises that branch.

**Guard form that survives:** drive `tick` end-to-end with a `running`, `rework_count>0` row
whose window reads idle-at-empty-prompt (mirroring the existing dead-window and stuck-rework
watchdog tests' fixture shape), patch `REWORK_PROMPT` to reference an unknown var so
`_renudge_prompt` raises, and assert the row actually reaches `changes_requested` with the
`rework re-nudge` reason in `last_error` — not left `running` forever. Isolating this from the
tick's OWN rework-respawn loop (which re-claims any `changes_requested` row with a free slot,
in the same tick) requires occupying the one concurrency slot with an unrelated running row, or
a second call site's failure overwrites `last_error` before the assertion runs, proving nothing
about the branch under test.

**Found:** CMX-373 rework round 1 (2026-09-15), PR #525. Closed by
`test_a_stuck_rework_with_a_bad_template_is_marked_failed_not_left_running_forever` in
`tests/test_dispatcher_rework.py`.
