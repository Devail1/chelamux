## 31. A differential guard cancels because BOTH sides of the diff are the mutation's victim


**Assertion form:** shape 20's own guard form, reused verbatim after the code it guards was
restructured. `test_timestamps_on_leaves_every_non_boundary_event_unchanged` drives every
non-timestamp event through the endpoint TWICE — once with the feature flag off, once on —
and asserts the two HTTP responses are byte-identical. That was sufficient when the branch
it guards read `if event in TIMESTAMP_EVENTS and FLAG:` (shape 20): widening the membership
set only intercepts the event when `FLAG` is also on, so the off-run and on-run visibly
diverge and the differential catches it.

**Mutation that defeats it:** widen the membership set on a version of the branch that has
since moved ABOVE the flag check and above the fall-through entirely — `if event in
TIMESTAMP_EVENTS:` (flag consulted only *inside*, to pick the response body, not to decide
whether to intercept). Now membership alone decides interception, so a widened set swallows
the newly-added event **at both flag settings identically** — `{}` off, `{}` on, since a
`MessageDisplay`-shaped body with no `index` key also renders `{}`. The differential holds
(both sides moved together, shape 8's mechanism), every existing per-event side-effect test
still passes (none of them target this specific event), and the event is silently never
reaching `hooks.ingest` — a lost log record, not merely a different response — passes
unnoticed.

**Why this is distinct from shape 20:** shape 20 needed a *combination* of two
independently-toggled conditions (membership AND flag-on) that no fixture ever paired.
Here there is only one condition gating interception (membership) — the flag no longer
gates whether the branch intercepts, only what it answers with — so the differential
doesn't fail to *combine* two states, it fails because corrupting the shared gate moves
*both halves of the diff it computes* the same way (shape 8's mechanism), on code shaped
like shape 20's fix. A guard form proven against one structure of the code can stop working
when the surrounding structure changes, even with the guard's own text untouched.

**Guard form that survives:** don't compare the endpoint's two responses to each other —
assert, independently of the flag, that `hooks.ingest` actually ran for the event: read the
event log after each POST and assert it grew by one record of that event's own type. A
short-circuit that swallows the event can no longer hide behind "both responses matched,"
because neither POST reached the log at all.

**Found:** CMX-285 rework round 2 (2026-08-14), PR #356 — the judge widened `if event in
hooks.TIMESTAMP_EVENTS:` (now sitting above `hooks.ingest`, CMX-285's own restructuring of
the CMX-277/CMX-283 branch) to `... or event == "Stop"` in `chela/dashboard/app.py`. The
CMX-283 exhaustive differential test stayed green — `Stop`'s response was `{}` at both flag
settings, matching itself — while `hooks.ingest` never ran for `Stop` at all. Closed by
adding a same-test assertion that `event_log.read()` grew by one `hook.stop`-typed record
per POST, alongside a sibling test
(`test_endpoint_message_display_survives_a_malformed_body`) for the companion mutation that
dropped the `isinstance(body, dict)` guard on the same branch's early-return path, which no
existing `MessageDisplay` test exercised with a non-dict body.
