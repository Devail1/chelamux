## 374. A two-channel failure check is fed only the failure that reports on the other channel

**Assertion form:** `not_done = result.get("status") == CANNOT_VERIFY or bool(result["failed"])`
(here: `sweep_nightly.run`, deciding whether an owed survivor's filing went through). The step it
checks can fail two ways that report on DIFFERENT channels: the filer raising appends to
`failed`, while a refusal BEFORE the filer runs (the filed-key store is unreadable) sets only
`status` and leaves `failed` empty. The test injects the filer raising and asserts the survivor
stays owed. It reads as "a filing that did not go through keeps the survivor owed".

**Why that's a gap:** the injected failure lights `failed`, so the `failed` disjunct alone
decides the outcome and the `status` disjunct is never the one that matters. Drop it and the
test holds; the early refusal now settles the survivor as `ran`, and it is lost for good,
because its PR is already out of the sweep window. This differs from [[334d|shape 334d]] in
where the gap comes from: the operands read different OUTPUTS of one step, so a fixture can't
feed one without first knowing which failure path writes to which output.

**Mutation that defeats it:** `unfiled = bool(result["failed"])`.

**Guard form that survives:** list every way the checked step can fail and which field each one
writes, then add one fixture per failure path. For the early refusal, assert the field the other
disjunct reads is EMPTY (`failed == []`, the filer never called). That proves the remaining
disjunct is the only signal. Then assert the outcome: still owed, and no `ran` drop logged.

**Found:** CMX-374 rework round 1 (2026-11-12), PR #923. Fixed by
`test_an_owed_survivor_whose_filing_was_refused_stays_owed` (a torn `filed.json`).
