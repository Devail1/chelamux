## 432c. A primary check and its fallback are fed by the same fixture, so deleting the primary changes nothing

**Assertion form:** code asks an authority first and falls back to a local copy only when the
authority cannot be asked: `ls-remote origin <branch>` (the remote), then this clone's
`refs/remotes/origin/<branch>`. The test sets up "the remote already has this branch" and
asserts the dispatcher picks `<branch>-2`. It does that with `git push origin dev:<branch>`
from the test's own clone. A push from a clone also writes that clone's tracking ref. So the
fixture made the authority and the fallback agree, and the test passes whichever of the two
answered.

**Mutation that defeats it:** skip the primary (`if False and out.returncode in (0, 2)`), so
every answer comes from the local tracking refs. The suite stays green: the only fixture
that creates a remote branch also creates the local ref that the fallback reads. In
production the two do disagree. Another clone or a human pushes the name (no local ref, and
chela reuses a taken branch), or origin deletes a merged branch while the local ref stays
(chela suffixes a name that is free).

The same round had two more cases of one channel standing in for two. `FakeLinear` could
refuse an archive only by raising, so Linear's `success: false` on a 200 was never sent and
`ok = True` survived. And the fake could not return a malformed open record at all, so
"drop only that record" (versus `return []`, which empties the open set) was never run.

**Guard form that survives:** build the fixture so the primary and the fallback DISAGREE,
both ways. Push the branch from a second clone (assert this clone has no tracking ref for
it), so only the remote knows it's taken. Then create a stale tracking ref with
`git update-ref` and no remote branch, so only the remote knows the name is free. Prove the
fallback separately with a remote that cannot answer. For a fake transport, give it every
reply shape the real API has (raise, `success: false`, a malformed record next to good
ones), and assert what happens to the good records next to the bad one.

**Found:** CMX-432 round 3 (2026-10-02), PR #583 — `chela/dispatcher.py`
(`_remote_branch_exists`), `chela/sources/linear.py` (`archive_issue`, `list_open_tasks`,
`_publish_count`). Closed by `tests/test_linear_source.py::test_a_branch_only_the_remote_has_is_never_reused`,
`test_a_stale_tracking_ref_the_remote_deleted_does_not_take_the_name`,
`test_an_archive_refused_with_success_false_is_a_failed_archive`,
`test_a_malformed_open_record_drops_only_that_record` and
`test_publishing_one_teams_count_keeps_every_other_teams`.

**Related:** [432b](432b-a-discriminator-whose-fixtures-all-sit-on-the-side-it-accepts.md):
there the fixture helper could build only members. Here it can build only the case where both
sources agree.
