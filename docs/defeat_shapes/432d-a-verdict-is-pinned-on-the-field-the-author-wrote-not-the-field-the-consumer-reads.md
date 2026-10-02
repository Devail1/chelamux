## 432d. A verdict is pinned on the field the author wrote, not the field the consumer reads

**Assertion form:** an adapter returns two fields for one fact. `Task.terminal_state` says
*which* terminal verdict (`"done"` / `"canceled"`), and `Task.state` says `"closed"` /
`"open"`, which is the field G1's reconcile actually acts on. The canceled-vs-done test
asserts `terminal_state` for every case. The `state` test (archived ⇒ closed) uses only DONE
and archived issues. So nothing ever reads `state` on a CANCELED issue, which is the one
place where "terminal" and "done" give different answers.

**Mutation that defeats it:** `state="closed" if terminal == "done" else "open"`. Every
fixture where `state` is checked is also `"done"`, so the narrowed predicate agrees with the
real one everywhere it is observed. In production, a run whose issue was canceled reads OPEN
forever and never reconciles.

The same round found three more fakes that answered whatever the query said:

- `FakeLinear` matched the `includeArchived` and `nin` filters against the query TEXT, but
  not the team filter. `eq: $team` → `neq: $team` returned the same issues, because every
  fixture issue was in that one team.
- A "malformed response" was only ever a malformed RECORD inside a good connection, so the
  connection-missing branch was never reached, and `return nodes` (`[]`) in place of the
  raise survived.
- The run-resolution tests had at most one tracker-identifier run left after the
  preference, so `len(x) == 1` → `if x` (pick the first of two) was never tried.

**Guard form that survives:** assert the field the CONSUMER reads, across every value of
the discriminator (here: every state type × archived/not, with `state == "closed"` ⇔
terminal). Make the fake apply EVERY filter it is sent from the query text, and give it data
that the filter must exclude (a second team that shares issue numbers). Feed the read path
every response shape that is not the thing it asked for (no key, `null`, wrong type, on page
1 and on a later page). For an "else None when ambiguous" rule, build the ambiguity it
exists for: two candidates still tied after every preference, in both orders.

**Found:** CMX-432 round 4 (2026-10-02), PR #583 — `chela/sources/linear.py` (`_task`,
`OPEN_ISSUES_QUERY`, `_paged`), `chela/dispatcher.py` (`resolve_run`). Closed by
`tests/test_linear_source.py::test_state_is_closed_exactly_when_terminal_state_is_set`,
`test_the_open_set_reads_only_this_teams_issues`,
`test_a_response_without_an_issues_connection_is_a_failed_read`,
`test_a_missing_connection_on_a_later_page_is_a_failed_read` and
`test_two_tracker_runs_left_after_the_preference_resolve_to_none`, plus the Linear arm of
`tests/test_tracker_fetch_by_ids.py`.

**Related:** [430](430-a-cli-fake-returns-every-field-whatever-the-projection-asked-for.md)
and [430b](430b-a-lookup-fake-answers-the-same-whatever-ids-it-was-asked.md): fakes that
ignore what they were asked.
[432b](432b-a-discriminator-whose-fixtures-all-sit-on-the-side-it-accepts.md): every
fixture sits on one side of the discriminator.
