## 152. A dedupe behind an eligibility conjunct is fixtured only with an ineligible duplicate

**Assertion form:** the CMX-152 boot pass builds one list of rows to relaunch. It starts with
the sessions an earlier pass resumed (`carried`), then adds the gather rows. A dedupe should
stop a gather row from launching a session that is already carried:
`newest = {v.session_id: None for v in carried}`, then
`if v.manual_command() and newest.setdefault(v.session_id, …) != v.stamped_epoch: continue`.
The test that covered it put a gather row of the carried session into the fixture and
asserted `spawned == [resume S1, resume S2]`, meaning S1 was launched once. But that gather
row had `cwd=None`. `manual_command()` is `None` without a cwd, so the `and` short-circuited
and the dedupe was never evaluated. The duplicate got through, and then `resume()` skipped it
for its own reason ("no cwd"). That gave the same `spawned` list.

**Mutation that defeats it:** `newest: dict = {v.session_id: None for v in carried}` →
`newest: dict = {}`. The full suite stayed green (8198/8198). With an *eligible* duplicate (a
gather row that has a cwd), the mutant launches `claude --resume S1` twice in one pass and
records S1 twice in the owed list. No fixture ever had one.

**Why it slips past review:** the fixture really does contain the duplicate, and the
assertion really does check "launched once". It reads as a direct test of the dedupe. What
it misses is that the duplicate fails an *earlier* conjunct of the same `if`, and that a
downstream stage refuses that same ineligible row anyway. Two independent filters produce
one end state, and the fixture only exercises the one that isn't under test. This is the
dedupe-shaped case of [[420|entry 420]] (a downstream fail-closed produces the gate's end
state), where the bypass comes from the conjunct order described in [[35|entry 35]].

**Guard form that survives:** make the duplicate pass every other filter, so that only the
dedupe stands between it and a second launch. Give it a cwd, a session id, and a
`MANUAL` verdict, which is everything downstream needs to launch it for real. Then assert the
side effect counted per key (`spawned.count(resume S1) == 1`), the absence of a refusal
(`not_resumed == []`), and the persisted state (owed list has S1 once). Keep the ineligible
duplicate fixture too, because it pins a different behaviour (an incomplete row is still
reported). Check the result by emptying the dedupe's seed by hand and watching it go red.
