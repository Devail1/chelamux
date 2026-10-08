## 26b. An oldest-item threshold is fixtured only with items that are all past it

**Assertion form:** an alert fires when the OLDEST item in a queue has waited longer than a
threshold — `oldest_held` picks `min(stamped, key=ts)` (CMX-26's held-inbox push and its
`chela doctor` fact). The tests build the queue with one event, or with several events stamped
a few seconds apart (`T0`, `T0 + 10`), then tick well past the threshold and assert one push.

**Mutation that defeats it:** `min(stamped` → `max(stamped`, so the alert measures the NEWEST
event. Every fixture is either a single event, where oldest and newest are the same item, or a
cluster that crosses the threshold together. Measured from the newest, the age is still past the
threshold by the time the test ticks, so the push fires and its message matches. The suite
stayed green (6123 passed). In production a busy inbox keeps getting fresh events, so its newest
item is always young. The alert would never fire, and that is the incident it exists for.
`queue[0]` and `queue[-1]` pass the same fixtures too.

**Guard form that survives:** fixture the aggregate's candidates on OPPOSITE sides of the
threshold, in a queue order that does not match the age order. The oldest stamped event should
sit in the middle, behind an unstamped event, with the newest only 1s old when the oldest
crosses the threshold. Then `min`, `max`, `queue[0]` and `queue[-1]` each choose a different
event, and only `min` fires. Assert that the push is silent 1s before the threshold, fires 1s
after it, and names the OLDEST event's id, so a wrong pick that still fires goes red too. Drive
every reader of the aggregate from the same fixture, here both the push and the doctor fact.

**Related:** [[306|entry 306]] (a single-item fixture collapses every candidate source) and
[[432b|entry 432b]] (a discriminator whose fixtures all sit on the side it accepts). This entry
is the aggregate case: several items exist, but they all sit on one side of the threshold.

**Found:** `tests/test_stall_alerts.py` (CMX-26, round 3, PR #610). Closed by `_mixed_queue()`
and the tests built on it.
