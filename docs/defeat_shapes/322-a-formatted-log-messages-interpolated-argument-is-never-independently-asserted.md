## 322. A formatted log message's interpolated argument is never independently asserted

**Assertion form:** the guard pins the STATIC parts of a formatted log/error message —
literal words in the template, an interpolated value that's *also* asserted elsewhere
(a window id, a status word) — but never reads back one of the OTHER `%s`/`{}` slots the
same call interpolates.

**Mutation that defeats it:** blank one of the unchecked interpolated arguments at the call
site (`msg.content_type` → `""`). The template string, the literal words around it, and every
other slot the tests do check are untouched, so the rendered message still contains
`"permanently dropped"` and the window id the assertions look for — it just silently drops the
one piece of information (*what* was dropped) that slot existed to carry. The suite stays
green because nothing ever read that slot back.

**Guard form that survives:** for a log call with N interpolated arguments, assert each one
is present in the rendered message individually — not just the literal template text around
them. Wrapping the check in something unambiguous (`"(text)" in message`, not bare `"text" in
message`) also guards against the value coincidentally matching a substring of the static
prose.

**Found:** `tests/test_telegram_relay.py::test_relay_logs_permanent_drop_with_window_id_when_both_attempts_fail`
and its `RegistryRelay` twin (CMX-322 rework round 1, PR #408). `_notify_drop`
(`chela/telegram/relay.py`) logs
`"telegram message permanently dropped for %s (%s)%s" % (window_id, msg.content_type, suffix)`.
The tests asserted `"@1" in errors[0].message` (the window id) and `"permanently dropped" in
errors[0].message` (the static template text) but never read back `msg.content_type` — so the
judge blanking that argument left the rendered line as `"...permanently dropped for @1 ()"`
with the suite still 100% green. Fixed by adding `assert "(text)" in errors[0].message`,
parenthesized so it pins the interpolated slot's position rather than merely the word "text"
appearing somewhere in the message.
