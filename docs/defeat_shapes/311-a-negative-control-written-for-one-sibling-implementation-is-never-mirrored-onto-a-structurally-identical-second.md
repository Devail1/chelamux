## 311. A negative control written for one sibling implementation is never mirrored onto a structurally identical second

**Assertion form:** two classes implement the same contract in parallel — `TelegramRelay` and
`RegistryRelay` both end their `on_message` in the same three lines: try MarkdownV2, fall back
to plain text, and `log.error(...)` a "permanently dropped" line only if BOTH attempts fail.
Both got a positive-control test (`..._logs_permanent_drop_..._when_both_attempts_fail`,
asserting the ERROR line fires). Only `TelegramRelay` got the matching negative control
(`..._logs_nothing_extra_when_the_plain_text_fallback_recovers`, asserting the ERROR line does
NOT fire when the plain-text retry succeeds). `RegistryRelay`'s copy of that second test was
never written — the two classes read as "the same feature, twice," and a reviewer who sees the
first class fully guarded reasonably assumes the second inherited the same coverage, since the
code is nearly line-for-line identical.

**Mutation that defeats it:** on the UNGUARDED sibling only, make the success branch fall
through to the drop log instead of returning — `if self._sender(..., thread, **kw): return`
becomes `if self._sender(..., thread, **kw) and False: return`. `RegistryRelay.on_message` now
logs a permanent-drop ERROR for every message whose plain-text fallback actually succeeded.
Every existing `RegistryRelay` test stays green: the positive control (`fail_all=True`) never
reaches the mutated line's changed behavior — a fully-failed send already falls through to the
ERROR log by design, mutated or not — and no other `RegistryRelay` fixture ever drives the
recovering-fallback path at all. `TelegramRelay`'s own negative control, sitting right next to
the vulnerable code in the same file, catches nothing because it exercises a different class.

**Why the sibling's coverage doesn't transfer:** a negative control proves silence for the
specific object under test. `TelegramRelay`'s recovers-quietly test says nothing about
`RegistryRelay` — same log line, same three-line shape, but a different `on_message`, a
different `self._sender` call, a different code object entirely. Visual near-duplication
(literally copy-pasted with a `thread` argument threaded through) reads as proof of shared
behavior; it is only proof of shared *intent*. Each class needs its own fixture actually
calling its own method before the property is established for it specifically.

**Guard form that survives:** when N sibling implementations share a contract and a suite has
both a positive and a negative control for implementation 1, `git grep` for the other N-1
classes implementing the same method name/contract and confirm each carries its OWN copy of
every control — not a control with a similar name, one that actually instantiates that
sibling and drives its own call path. Do this before considering a shared-shape guard closed on
more than one implementation.

**Found:** `chela/telegram/relay.py`'s `TelegramRelay.on_message` / `RegistryRelay.on_message`
(CMX-311 rework round 2, PR #387). `tests/test_telegram_relay.py` had
`test_relay_logs_nothing_extra_when_the_plain_text_fallback_recovers` for `TelegramRelay` but
no `RegistryRelay` equivalent, even though
`test_registry_relay_logs_permanent_drop_with_window_id_when_both_attempts_fail` sits right
next to where it would go. `chela judge` short-circuited `RegistryRelay`'s plain-text success
check with `and False` in a throwaway checkout; `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
stayed green (3224 passed) with the corruption in place. Closed by adding
`test_registry_relay_logs_nothing_extra_when_the_plain_text_fallback_recovers`, mirroring the
`TelegramRelay` test onto `RegistryRelay`: a `fail_markdown=True` stub, `on_message` called
once, and an assertion that no ERROR record was emitted.

**See also:** [[07|shape 7]] — also about coverage that only reaches one of several
structurally similar routes to the same behavior, but shape 7 is one function called from
multiple call sites inside a single implementation; this shape is the same guard gap one level
up, across multiple independent implementations of the same contract.
