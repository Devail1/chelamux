## 432b. A discriminator whose fixtures all sit on the side it accepts

**Assertion form:** code narrows a set by some property: only relations typed `blocks`, only
ids carrying this team's key, any tracker key shaped like `api_key|token|secret|password`,
`canceled` kept apart from `completed`. The tests are thorough about what happens to the
members. A blocker that is open holds the task, a done one releases it, an archived `Done`
issue reads as terminal, a `tracker.api_key` is refused. Each test passes the right way and
reads as covering the filter. But every fixture was built by the same helper, and that helper
only makes members: `blocked_by()` always writes `type: "blocks"`, every requested id is
`CMX-N`, the only refused key ever tried is `api_key`, and the only terminal issue is `Done`.

**Mutation that defeats it:** drop the discrimination. Remove `rel.get("type") != "blocks"`
and a `related` link would hold a task. Widen `CMX-(\d+)` to `[A-Za-z]+-(\d+)` and `ENG-5`
returns the team's own CMX-5. Shrink the regex to `api_?key` and a `token:` in WORKFLOW.md
reaches a public repo. Map `canceled` to `done` and canceled work reads as shipped. The suite
stays green in every case, because no fixture is something the filter is supposed to turn
away, so nothing tells a filter from no filter.

**Guard form that survives:** for each discriminator, put at least one non-member through the
same path and assert it is rejected, then show the member is still accepted. Use a `related`
or `duplicate` relation to an open issue (claimed), next to the same link typed `blocks`
(held). Try `ENG-5`, `CMX-5x`, `XCMX-5` and a 12-hex TODO.md-era id: none of them comes
back, and the transport is never called. Parametrize over every alternative in the regex's
alternation, not only the one in the brief. Give each terminal type its own fixture and
assert the value, not "is terminal". When a fixture helper can only build members, that
helper is the defect, so give it a parameter for the other side.

**Found:** CMX-432 round 2 (2026-10-02), PR #583 — `chela/sources/linear.py` (`_blockers`,
`_number_of`, `_CREDENTIAL_KEY_RE`, `_task`'s terminal state). Closed by
`tests/test_linear_source.py::test_only_a_blocks_relation_holds_a_task`,
`test_an_id_outside_this_team_is_never_this_trackers`,
`test_every_credential_shaped_workflow_key_is_refused` and
`test_a_canceled_issue_is_canceled_not_done`.

**Related:** [337](337-a-guarded-condition-proven-only-where-a-sibling-condition-already-rejects.md)
has non-members, but each one fails two gates at once. Here there are no non-members.
[365](365-a-fixtures-numbering-and-null-shape-coincide-with-the-sort-key-it-exercises.md)
is the same round's tie-break survivor: the fixture's input order already equals the order
the tie-break produces.
