## 430b. A lookup fake answers the same whatever ids it was asked, so narrowing the request is invisible

**Assertion form:** code asks a by-id lookup about a SET of ids and treats any id missing
from the answer as "gone" (`gone = ids - {t.id for t in fetch(sorted(ids)) if open}`). The
test fakes the lookup with a canned snapshot fixed at construction (`_Fetch([open1,
closed1])`) and asserts the classification it produces — often with expected outcomes
(`closed1`, `absent1` gone) that the canned snapshot would yield no matter what was asked.

**Why it doesn't guard:** the fake ignores its argument. A real lookup only returns records
it was asked about, so an id dropped from the REQUEST is absent from the ANSWER — and absent
means gone. With a canned answer, the request can be truncated, filtered or emptied and every
assertion still passes, because the answer never depended on the question. Worse, when the
expected verdict for the dropped id is itself "gone" (the `absent1` arm), the corruption and
the correct code agree on the fixture.

**Mutation that defeats it:** (CMX-430) `snapshot = fetch(sorted(ids))` →
`snapshot = fetch(sorted(ids)[:1])`. Only the first candidate is refreshed; every other live
run reads as absent ⇒ gone ⇒ `done`, window killed, worktree deleted. Suite green.

**Guard form that survives:** make the fake's answer a function of the question — e.g. report
every id it was ASKED about as open — and assert that a multi-id candidate set yields
**nothing** gone. An id the code forgot to ask about is then the only way to read as gone, so
any narrowing of the request goes red. Record what was asked too (`sorted(asked) ==
sorted(ids)`), but the open-for-what-was-asked consequence is the part that pins the
invariant rather than the call shape.

**Related:** [[430|shape 430]] (a CLI fake returns every field whatever the projection asked
for) — the same "the fake ignores its input" blind spot, on the id axis instead of the field
axis.
