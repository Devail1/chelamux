## 174. A relabel gated on one verdict is fixtured with a runner that can only produce that verdict

**Assertion form:** an outcome is relabelled only when two things hold — the selection
tagged it (`sel.untested`) AND the verdict is the one the label belongs to
(`outcome.verdict == SURVIVED`) — and the guard asserts the relabel happens: "a TS file no
test reaches survives as `UNTESTED — …`". The fixture's stand-in test runner (a fake
`vitest`) always exits 0, so every mutation in the file, on every file, is SURVIVED.

**Mutation that defeats it:** drop the verdict conjunct — `if sel is not None and
sel.untested and outcome.verdict == SURVIVED:` → `if sel is not None and sel.untested:`.
Every outcome the fixture can produce is already SURVIVED, so the conjunct is always true
and deleting it changes nothing the test can see — while a real KILLED or INVALID outcome
on an untested file now reads `UNTESTED — …the guard fired`, a survivor label on a mutation
that was caught (or never measured).

**Why it looks covered:** the positive case is pinned exactly (verdict, blocking list, the
reason's prefix, the full-suite call). But the conjunct only acts on a verdict OTHER than
SURVIVED, and the fake runner is structurally unable to emit one. A fake that can only say
"green" makes every verdict-gated branch downstream of it dead. Cousin of [[348|shape 348]]
(a conjunct never independently pinned), but the cause here is the fake, not the fixture
choice: no fixture in the file *could* have flipped it.

**Guard form that survives:** give the fake a way to produce each verdict the gate
discriminates — here a marker in the mutated source makes it fail one test with the count
held (KILLED) or collapse the count (INVALID) — and on the SAME untested file assert the
precondition (`plan.untested`), the verdict, and that the reason equals what the
adjudicator gives for those measurements, with no relabel. Pin the tag conjunct from the
other side too: an untagged file (an unreached `.js`) on the same full-suite path survives
with its plain reason.

**Found:** CMX-174 rework round 1, PR #731. `chela/judge.py` `_measure()`; fixed in
`tests/test_judge_select_vitest.py`
(`test_an_untested_ts_file_that_is_not_a_survivor_keeps_its_own_verdict_and_reason`,
`test_an_unreached_js_file_survives_as_a_plain_survivor_not_UNTESTED`).
