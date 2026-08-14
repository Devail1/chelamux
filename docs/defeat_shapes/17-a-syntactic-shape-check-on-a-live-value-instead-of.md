## 17. A syntactic-shape check on a live value instead of pinning what produced it

**Assertion form:** the guard checks that a value produced by a live mechanism (a clock, a
random source, an external call) has the right *shape* — length, character positions, a regex
— rather than pinning that the mechanism was actually invoked to produce it. `len(stamp) == 8
and stamp[2] == ":" and stamp[5] == ":"` reads exactly like "this is an HH:MM:SS timestamp"
and is satisfied identically by a real clock read and by a hardcoded literal.

**Mutation that defeats it:** replace the call to the live mechanism with a fixed value of the
same shape — `time.strftime("%H:%M:%S")` → `"00:00:00"`. Every shape assertion still passes;
the feature's entire claim (this is a *live* timestamp) is gone.

**Why this is distinct from shape 5 and shape 10:** shape 5 is about reading a constant off
source instead of the rendered/wired value — here the value genuinely is the rendered output,
not source. Shape 10 is a mechanism that is *unobservable outside contention* (a kernel-picked
port vs. a lucky guess are bit-for-bit identical, so no test can pin the mechanism directly,
only declare the gap). This shape is the easy case: the mechanism **is** directly observable —
the module holds the live source (`time`) as an attribute, so a test can monkeypatch it and
assert the exact value that flowed through, no declared gap required.

**Guard form that survives:** monkeypatch the live source itself
(`monkeypatch.setattr(hooks.time, "strftime", lambda fmt: "12:34:56")`) and assert the
rendered output equals the exact value the patched mechanism returned. This proves the code
path actually asks the mechanism, rather than merely producing something shaped like its
answer.

**Found:** CMX-277 rework round 2 (2026-08-14), PR #348 —
`test_timestamp_response_carries_the_proven_persistent_envelope`'s shape check
(`len(stamp) == 8 and stamp[2] == ":" and stamp[5] == ":"`) survived the judge's
`ts = time.strftime("%H:%M:%S")` → `ts = "00:00:00"` mutation. Closed by adding
`test_timestamp_response_asks_the_module_clock_not_a_fixed_string`, which monkeypatches
`hooks.time.strftime` and asserts the exact rendered `systemMessage`.
