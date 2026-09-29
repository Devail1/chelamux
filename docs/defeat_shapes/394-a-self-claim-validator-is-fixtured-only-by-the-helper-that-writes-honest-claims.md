## 394. A self-claim validator is fixtured only by the helper that writes honest claims

**Assertion form:** a reader trusts a record only if the record is consistent about itself
and unique — `<pid>.json` must say `pid: <pid>`; exactly one pane may claim a session. The
tests build every record with ONE fixture helper (`_registry(pid, sid, …)`), which writes
the file's name and its `pid` field from the same argument and gives each session a single
claimant. Every record the suite ever feeds the reader is therefore honest and unambiguous by
construction, so the refusal clauses — "the file names a different pid", "two claimants" —
never see an input they would refuse. The positive tests (and a pid-reuse refusal test that
defeats a DIFFERENT clause) read as full coverage of the validator.

**Mutation that defeats it:** delete either refusal — `or data.get("pid") != pid` → `or
False`, or `claims[0] if len(claims) == 1 else None` → `claims[0] if claims else None`. The
honest single-claimant fixtures take the same path either way, and the suite stays green.

**Guard form that survives:** for each clause of the validator, write the one record that
ONLY that clause refuses: the helper's output mutated in exactly the field the clause checks
(the `pid` field rewritten, everything else — procStart, sessionId — still valid), and a
second claimant registering the same session. Assert None, and pair each with a control
that the same fixture minus the defect resolves — so the red comes from that clause and not
from a fixture that was broken some other way. A fixture helper that cannot produce a
dishonest record cannot test a check for dishonesty.

**Related:** shape 57 (a not-found arm never armed) is the same blindness for a missing
record; this is it for a present-but-lying one. Shape 308 (two gating conditions only armed
together) is the multi-clause cousin.

**Found:** `chela/sessions.py` `registry_entry` / `wid_claiming_session`, tests in
`tests/test_orchestrator_pin.py`, CMX-394 (PR #546, judge round 1).
