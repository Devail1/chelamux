## 79c. A late observer's "skip" branch is never fed, because every test ticks it before the state moves

**Assertion form:** a poller mirrors a store to another surface — the Telegram override relay
posts each request ONCE (with Approve / Deny buttons) and later edits it to its outcome. It
has a branch for an item it sees for the FIRST time already past the state it announces
("decided before the bridge saw it ⇒ post nothing"). Every test creates the request, ticks
the relay at once (so it is posted while still `pending`), and only then decides it. The
"first seen already decided" ordering never happens in the suite.

**Mutation that defeats it:** dead-code the skip (`if state != PENDING: return` →
`if False and …`). A merged / denied / expired request now gets a NEW message with live
Approve / Deny buttons — and 6621 tests stay green, because no fixture ever hands the relay
a decided request it has not already posted.

**Guard form that survives:** drive the other ORDER. Move the request to each non-pending
state first (`denied`, `merged`, `failed`, `expired`, the transient `merging`) WITHOUT a tick,
then tick the relay (twice) and assert no post and no edit. The question to ask of any
observer: "what does it do with an item it first meets late?" — if no test makes it meet one
late, that branch is untested. Same round, catalogued elsewhere: the CLI's `--deny` driven
only at its default (shape 411), the bot flag proven on the inner function, never through the
handler that derives it (shape 338), and `pending()`'s own TTL filter shadowed by the sweep
that every caller runs first (shape 7c).

**Found:** CMX-79 rework round 2 (2026-10-11), PR #642 — `chela/telegram/overridewatch.py::
OverrideRelay._sync`.
