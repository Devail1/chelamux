## 352. A new pure render helper exported for testability is never imported by any test

**Assertion form:** none. A new feature (issue #462's task-progress chip) adds a pure
render helper (`_taskProgressChip` in `chela/dashboard/static/js/dispatcher.js`) and
exports it from the module's `export { ... }` list — the project's own established pattern
for making a helper unit-testable without driving the full DOM (see
`tests/taskmodal_judge_badge.test.mjs`'s comment citing this exact convention). The export
exists; nothing ever imports it. Two call sites consume its return value
(`dispatcher.js`'s `_cell('Tasks', _taskProgressChip(r.tasks))` and `kanban.js`'s
`const taskChip = _taskProgressChip(card.tasks);`), and a Python sibling
(`chela/tasklists.py::read_tasks`) carries two defensive clauses guarding the same
feature's data (`or not subject`, `if not isinstance(obj, dict)`) — none of the three had a
single fixture exercising the case they exist for. The PR "read fine" in review; nothing
had ever actually run any of it.

**Mutation that defeats it:** any of — drop either call site's argument for the helper's
return value down to a constant (`_taskProgressChip(r.tasks)` -> `''`); dead-code the
helper's own label or tooltip line (`if (tasks.in_progress) lines.push(...)` -> `if (false
&& tasks.in_progress) lines.push(...)`); dead-code either Python defensive clause (`or not
subject` -> `or (False and not subject)`, `if not isinstance(obj, dict):` -> `if False and
not isinstance(obj, dict):`); or swap a request-scoped variable for a fresh per-row fetch
(`tasklists.progress_for_run(r, session_entries, current_epoch)` ->
`tasklists.progress_for_run(r, sessionids.entries(), epoch.current())`). Every one of these
seven mutations parsed and left `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` at 3793 passed,
0 failed — because the exported helper had zero callers in the test suite, the two wiring
call sites had zero DOM-level assertions, the Python clauses had zero fixtures supplying
the case they guard, and the once-per-request contract had zero call-count assertion.

**Guard form that survives:** for a feature spanning a pure helper + N call sites, write N
DOM-level wiring tests (one per real call site, driving the real render function and
reading the rendered node back — not a source grep, see [[7|shape 7]]) PLUS a fixture for
every defensive clause's actual trigger condition (present-but-empty, not just
absent-or-wrong-type; non-dict-but-valid-JSON, not just invalid-JSON-syntax) PLUS, for a
"fetched once, reused everywhere" contract, a call-count assertion on the shared dependency
rather than only a correctness assertion on its result. `tests/task_progress_chip.test.mjs`
closes the JS half (2 wiring tests + a tooltip test, driving `renderDispatcher` and
`renderKanban` through a real jsdom and reading `.task-progress-chip` back);
`tests/test_tasklists.py` and `tests/test_tasklists_dispatcher_api.py` close the Python
half (two new fixtures + a `monkeypatch`-counted call-count assertion on
`sessionids.entries`/`epoch.current`).

**Found:** CMX-352 rework round 1, PR #465. The judge's own required-mutation-set verdict
named all seven mutations above verbatim; none were newly discovered by this entry — the
entry is the missing catalog record for why a feature this size shipped with none of them
guarded in the first place.
