## 20. A short-circuit's membership set is proven, but never proven together with the state that would make widening it dangerous

**Assertion form:** a dispatcher-style function has an early `if event in SOME_SET and FLAG:
return X` that is meant to intercept only a couple of named events and fall through to
everything else unchanged. One test pins the set's exact membership (`SOME_SET ==
frozenset({...})`); other tests drive each member event through the branch with `FLAG` on;
still other tests drive the events the branch is protecting (a *different* event, further
down the function) with `FLAG` at its real default. No test ever combines "an event the later
branch cares about" with "`FLAG` on" — because every fixture that turns `FLAG` on also happens
to only POST the early-branch's own events, and every fixture that POSTs the later branch's
event happens to run at `FLAG`'s real (off) default.

**Mutation that defeats it:** widen the membership check with an `or event == "<later branch's
event>"` clause. The early branch now also intercepts and returns for that event whenever
`FLAG` is on — silently skipping whatever the later branch does (a side effect, not just a
different return value) with `FLAG` in the one state no fixture ever paired with that event.
The membership-equality test still passes (it never says the check is *only* membership,
just what the set contains); every early-branch test still passes (none of them touch the
later branch's event); every later-branch test still passes (none of them turn `FLAG` on).

**Why this is distinct from shape 12:** shape 12 is a loop that should stop at the first match
but is tricked into falling through to consult more entries. Here there is no loop — it's a
single boolean short-circuit whose *members* are proven correct in isolation, but never
proven not to swallow a sibling branch once independently-true guard conditions (set
membership, and a config flag) are combined. The gap is combinatorial coverage of two
independently-toggled conditions, not fall-through.

**Guard form that survives:** drive the later branch's event through the endpoint with the
early branch's flag deliberately turned ON, and assert two things at once — the response body
still has the *un-intercepted* shape (proving the early branch did not return early for this
event), and the later branch's own side effect still fired (proving control actually reached
it, not just that the return value looked right by coincidence).

**Found:** CMX-277 rework round 5 (2026-08-14), PR #348 — the judge's `if event in
hooks.TIMESTAMP_EVENTS and config.TERMINAL_TIMESTAMPS:` → `if (event in
hooks.TIMESTAMP_EVENTS or event == "PostToolUse") and config.TERMINAL_TIMESTAMPS:` mutation
in `chela/dashboard/app.py` survived because the flip to `TERMINAL_TIMESTAMPS` defaulting OFF
(round 2) meant every ON-state test only POSTed `UserPromptSubmit`/`Stop`, and every
`PostToolUse` test ran at the real (OFF) default — so no fixture ever POSTed `PostToolUse`
with timestamps ON, which is exactly the combination the mutation needs to steal
`gateanswer.gate_resolved()` and reproduce the CMX-54 regression (a held gate waiting out its
whole budget). Closed by
`test_timestamps_on_does_not_steal_the_post_tool_use_gate_resolution`, which sets
`TERMINAL_TIMESTAMPS = True`, POSTs `PostToolUse`, and asserts both the body is `{}` and
`gate_resolved` was still called.
