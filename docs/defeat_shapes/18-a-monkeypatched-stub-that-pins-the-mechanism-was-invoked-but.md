## 18. A monkeypatched stub that pins the mechanism was invoked, but discards which arguments it was invoked with

**Assertion form:** the guard from shape 17 — monkeypatch the live source and assert the exact
value the stub returned — proves the mechanism was *asked*, but the stub itself is written as
`lambda fmt: "12:34:56"`: it accepts `fmt` and throws it away. Any call, with any format string,
produces the same stubbed return value, so the assertion on that return value cannot tell two
different format strings apart.

**Mutation that defeats it:** change *what* is asked for, not *whether* it's asked —
`time.strftime("%H:%M:%S")` → `time.strftime("%d:%m:%y")`. Both calls still reach the
monkeypatched `strftime`, so the "mechanism was invoked" guard is untouched and still returns
the stubbed `"12:34:56"`. The rendered `systemMessage` is byte-identical either way, so the
shape-17 fix — which only ever inspects the return value — cannot distinguish a live *time*
stamp from a live *date* stamp. The docstring's claim (`HH:MM:SS`, local time) is now false for
a date-formatted string, and nothing failed.

**Why this is distinct from shape 17:** shape 17 is "was the mechanism invoked at all, versus a
hardcoded literal" — solved by monkeypatching the source and checking the output flowed through
it. This shape is one level deeper: *given* the mechanism was invoked, *which request* did the
code make of it? A stub that ignores its own arguments proves the former but is structurally
blind to the latter — the args never reach anything the assertion inspects.

**Guard form that survives:** capture the argument the code passed, not just the value the stub
handed back — `captured = []`; `monkeypatch.setattr(hooks.time, "strftime", lambda fmt:
captured.append(fmt) or "12:34:56")`; then assert `captured == ["%H:%M:%S"]` in addition to
asserting the rendered output. This pins the request, not just that a request happened.

**Found:** CMX-277 rework round 3 (2026-08-14), PR #348 — the judge's
`ts = time.strftime("%H:%M:%S")` → `ts = time.strftime("%d:%m:%y")` mutation survived round 2's
`test_timestamp_response_asks_the_module_clock_not_a_fixed_string` because its stub discarded
`fmt`. Closed by capturing `fmt` into a list and asserting its exact value.
