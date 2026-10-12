## 87. A chain of pre-push re-checks tests only the ones the brief named

**Assertion form:** a function runs N sibling fail-closed checks back to back before an
irreversible action (here: `chela.release.complete()` before pushing `main`, a tag and
`dev`). The brief named two of them ("dev still equals the cut commit", "main is still an
ancestor"), and those two each get a negative test. The implementer added the rest on their
own initiative — the local tag still points at the approved commit, the release commit is a
single commit on top of dev, the tag is not already on origin (or unreadable), plus the same
tag-on-origin check in `prepare()` — and every one of those is only ever reached on the path
where it passes.

**Mutation that defeats it:** `if X:` → `if False and X:` on any implementer-added check.
The happy-path test pushes exactly as before; the brief-named negatives fail on an EARLIER
check, so they never reach the neutered one. The suite stays green while chela would now
push a tag that origin already has, or a commit the operator never saw.

**Why it is easy to miss:** it is shape 12b repeated per sibling, but the trigger is
different — the test list is copied from the brief's acceptance criteria, so a check that
is not in the brief has no line in the test list at all. A second trap: an earlier,
overlapping check can catch the fixture first (here `git fetch`'s tag auto-follow made the
LOCAL-tag refusal fire before the origin-tag one), so the negative for check K must arm a
violation that passes checks 1..K-1.

**Guard form that survives:** one test per check in the chain, each arming exactly ONE real
violation that every earlier check still lets through (a foreign tag on a commit outside
main/dev's history, so fetch cannot bring it home; a record whose release commit sits two
commits above dev with its tag moved along), and asserting the absence of the side effect —
no push recorded, origin's `main`/`dev`/tag unchanged — not just an error string. Where a
later step would fail on its own anyway (e.g. `git worktree add -b` on an existing branch),
assert the named refusal's own text so the guard is not shadowed by the fallback. The
unviolated happy-path test is the negative control.

**Found:** CMX-87 (`chela/release.py` `complete()` / `prepare()`), guarded in
`tests/test_release.py` — `test_approve_with_the_tag_already_on_origin_pushes_nothing`,
`::test_approve_when_the_local_tag_was_moved_pushes_nothing`,
`::test_approve_when_the_release_is_not_one_commit_on_dev_pushes_nothing`,
`::test_prepare_refuses_when_the_tag_already_exists_on_origin` and siblings.
