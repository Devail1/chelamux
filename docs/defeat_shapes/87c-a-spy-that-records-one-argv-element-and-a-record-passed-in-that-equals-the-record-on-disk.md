## 87c. A spy records one element of the call, and the "caller's record" equals the stored one

**Assertion form:** a function pushes with a command line (`git push --quiet origin <refspec>`)
and the test spy records only the last argument (the refspec). A second function takes a record
from its caller but must act on the copy on disk; every test passes it the very record it just
loaded, so caller and disk agree. A fail-closed read (`if not live_main or ...`) is exercised
only with a readable value.

**Mutation that defeats it:** add `--force` (or a `+` refspec prefix) to the argv — the spy never
sees it and the happy-path origin accepts a fast-forward either way; read `rec.get("release_sha")`
instead of `stored.get(...)` — identical values; weaken `not live_main or ...` to
`live_main and ...` — the unreadable case (None) is never fed in.

**Guard form that survives:** pin the WHOLE argv of every push (exact tuple equality, plus no
force-ish flag); run a real bare origin whose target ref is rewritten to a non-ancestor right
before each push and assert git refused and origin kept the other commit (only true without a
force); give complete() a caller copy that LIES about every field it reads (git_dir, tag,
version, task_ids, dev_sha, release_sha) and assert the push, audit event and result follow disk;
feed None to every remote/ref read and assert nothing is pushed.

**Found:** CMX-87 judge round 9 (`chela/release.py` `complete()`), guarded in
`tests/test_release.py` — `test_every_push_is_spelled_plain_with_no_force_flag_and_no_plus_refspec`,
`::test_a_ref_that_moved_to_a_non_ancestor_during_the_pushes_is_refused_by_git`,
`::test_origin_main_unreadable_at_approval_fails_closed`,
`::test_complete_reads_every_field_from_the_stored_request`.
