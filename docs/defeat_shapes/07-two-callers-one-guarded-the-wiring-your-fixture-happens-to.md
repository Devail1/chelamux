## 7. Two callers, one guarded (the wiring your fixture happens to reach)

**Assertion form:** a test drives a function through *one* of its call sites and asserts the
right thing happens. The assertion is real, the fixture is honest, and the guard genuinely
fails when that path breaks.

**Mutation that defeats it:** break the function at a *different* call site. Nothing notices,
because no fixture in the suite ever drives that one. The reviewer reads a passing test named
after the behaviour and reasonably concludes the behaviour is covered — when what is covered is
one route to it.

**Guard form that survives:** enumerate the call sites (`git grep` the symbol — this is a
question with a countable answer, unlike a CSS-property space) and drive *each* one. If N
call sites exist, the suite needs N wiring tests, and the test names should say which route
each covers.

**Found:** twice in one day, 2026-08-13.
- `_syncSidebarActive` (CMX-257) has exactly two callers — `selectView` and `showAgentDetail`.
  Rounds 19-21 guarded the first exhaustively (class set, cue painted, row routed). The second
  hardcodes a *demoted* view id (`view === 'agent-detail' ? 'agents' : view`) and nothing drove
  it: blanking that literal left the sidebar with no lit row on every agent drill-in, with 3012
  tests green. Closed in round 23 by driving the real `chela.selectAgent` path.
- `latest_required_mutations` (CMX-269) is wired into the rework brief at two render paths —
  `_respawn_rework` and `_renudge_prompt`. Both new prompt tests drove `tick`, which reaches
  only one per run state; dropping the argument from the other stayed green.
- `judge_max_concurrent`'s floor=1 (CMX-278) has two entry paths into the value — the
  dashboard/`set_dispatch` *write* path, which `validate_dispatch` floors before it ever
  reaches storage, and the env-file *read* path (`CHELA_JUDGE_MAX_CONCURRENT`), which
  `dashboard_setting` resolves straight from `os.environ` and never runs through
  `validate_dispatch` at all. `test_judge_max_concurrent_floor_is_one_not_zero` drove only
  the write path; mutating the reader's own `max(1, ...)` to `max(0, ...)` — the last guard
  standing on the second path — left the suite green. Closed by a second test that sets the
  env var to `"0"` and asserts the reader still returns `1`.
- `knMd`/`knInline` (CMX-279 rework round 4, PR #350) has THREE call sites this PR edited —
  kanban.js:152 and taskmodal.js:156 (the title) were closed independently in round 3;
  taskmodal.js:116 (`_timelineHtml`'s `knMd(s.detail)` for the review-timeline body — the PR
  also edited this line, dropping the `'review.md'` argument) was never driven by any fixture,
  since every DOM test that reaches `openTaskModal` passes no `review_history`. Closed by a
  4th wiring test in `tests/taskmodal_render.test.mjs` that passes a `review_history` payload
  and reads the real `.task-modal-timeline-body` element back.
- `knMd`/`knInline` (CMX-279 rework round 5, PR #350) — recurred a SECOND time on the same
  symbol. `taskmodal.js:127` `_briefPane`'s `briefHtml(src)` call is a FOURTH call site (the
  header's own doc comment names it first, as the reason the module survives at all), and it
  was still unguarded going into round 5: both existing DOM tests in
  `tests/taskmodal_render.test.mjs` pass items with no `brief`/`body`/`raw`, so
  `briefSource(item)` resolves to `null` and `_briefPane` short-circuits to its "No brief
  recorded" note before ever reaching `briefHtml`. Closed by a 5th wiring test driving the real
  `openTaskModal` with an item carrying a markdown `brief` and reading `.task-modal-brief`
  back. The standing lesson from the second occurrence: counting call sites once is not
  enough — re-`git grep` the symbol every round a guard on it changes, since a caller added or
  edited in an earlier round of the SAME PR can still be the one nobody wired.

⭐ The judge caught the second one by proposing **a separate wiring experiment per call site
rather than guessing which was covered** — which is also the cheapest way to write the guard.
Two callers becomes N callers becomes "count them all, every round" — a shape doesn't stop
recurring just because it was closed once at a smaller N.
