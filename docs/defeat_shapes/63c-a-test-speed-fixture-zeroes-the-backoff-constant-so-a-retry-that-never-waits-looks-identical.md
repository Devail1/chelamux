## 63c. A test-speed fixture zeroes the backoff constant, so a retry that never waits looks identical to one that backs off

**Assertion form:** CMX-63 retries a transient Telegram media failure with a backoff,
`await asyncio.sleep(RETRY_BACKOFF[attempt - 1])`, `RETRY_BACKOFF = (1.0, 3.0)`. So the suite
wouldn't really sleep 4 s per test, every retry test took a `no_backoff` fixture that did
`monkeypatch.setattr(media, "RETRY_BACKOFF", (0, 0))`. The tests then asserted the attempt
count (`get_file_calls == len(RETRY_BACKOFF) + 1`), the final reply and delivery. Nothing
looked at how long the code waited.

**Mutation that defeats it:** `await asyncio.sleep(RETRY_BACKOFF[attempt - 1])` →
`await asyncio.sleep(0)`. The fixture had already set every scheduled wait to `0`, so the
mutated code and the real code did exactly the same thing under test: same attempt count,
same reply. The fixture made the test fast by removing the one value that told "backs off"
apart from "hammers immediately". The mutation agreed with the fixture, so no test could
fail. `len(RETRY_BACKOFF)` still read the right length, which made the guard look tied to
the constant when it only checked the constant's length.

**Guard form that survives:** keep the shipped constant in force and make the *sleep*
cheap, not the *schedule*. Swap only the module's own `asyncio` for a stand-in whose `sleep`
records each requested delay and returns at once (`monkeypatch.setattr(media, "asyncio", …)`,
not the global `asyncio.sleep`, which the event loop running the test also uses). Then
assert the recorded delays equal the live schedule, `sleeps == list(media.RETRY_BACKOFF)`,
plus `all(d > 0 …)`, and `sleeps == []` on the path that must not retry. A `sleep(0)`
records `[0, 0]` and goes red, and so does a wrong index or a frozen copy of the constant.
In general: if a fixture neutralizes a value to make a test fast, check whether that value
is what the guard should be observing. If it is, intercept where the value is *used*, don't
overwrite it at the source.

**Related:** [[357|entry 357]] (a cadence guard bounded only from above lets `sleep(0)`
through). There the recorder existed but checked only one side of the range. Here the
fixture set the value to the mutation's value before any check ran.

**Found:** CMX-63 rework round 2 (2026-10-10), PR #628. The judge applied
`sleep(RETRY_BACKOFF[attempt - 1]) → sleep(0)` to `chela/telegram/media.py`, and the full
suite stayed green (6408 passed).
