## 338. An optional constructor kwarg is proven by direct-construction unit tests but never checked at its one production call site

**Assertion form:** a relay class takes an optional kwarg that gates a whole feature
(`RegistryRelay(sender, registry, send_photos=...)` — without it, images are silently
ignored). Every behavioral test in the suite constructs the class *directly*, passing the
kwarg by hand (`RegistryRelay(stub, reg, send_photos=photos)`), so the kwarg's *effect* once
present is thoroughly guarded. But the ONE place in the codebase that builds the *production*
instance — `chela.main.cmd_telegram`, wiring `send_photos=bot.send_photos` — is never driven
by a test that reads back what it actually passed. The daemon-entrypoint tests that DO exist
for that same call (`test_the_no_inbound_daemon_starts_the_pane_thread`,
`test_cmd_telegram_warms_the_native_status_cache`) assert other things about the same
construction — the pane thread got started, the status cache got warmed — and simply never
look at this kwarg.

**Mutation that defeats it:** delete `send_photos=bot.send_photos` from the `RegistryRelay(...)`
call in `cmd_telegram`. Every relay-level test still passes `send_photos` explicitly at its own
construction call, so none of them notice; the daemon-wiring tests never touched this kwarg
either. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3536 passed, 0 failed) while
the shipped daemon could never post an image, ever — the entire image-relay feature dead in
production behind a suite that reads, at a glance, like it covers image relay end-to-end.

**Why this is distinct from [[07|shape 7]]:** shape 7 is a function with MULTIPLE call sites
where a fixture only ever reaches one of them — the fix is to enumerate callers and drive each.
Here there is exactly ONE call site; the danger isn't "which of several paths does my fixture
reach," it's that a class's constructor being unit-tested directly, over and over, manufactures
the *feeling* that its wiring is proven, when no test has ever read back what the single real
caller actually passed it. A thorough class-level suite and a thorough entrypoint-level suite
can each be complete on their own terms and still leave this exact gap between them.

**Guard form that survives:** spy on the class at its production entrypoint — monkeypatch the
symbol in the module the entrypoint imports it into (the same technique the file already used
for `PermissionGateWatcher`), drive the entrypoint itself, and assert the specific kwarg is
present *and* bound to the real dependency, not just non-`None`: check it's the actual bound
method (`send_photos.__self__` is a `BotSender` instance, `send_photos.__func__.__name__ ==
"send_photos"`), not a lookalike stub that would satisfy a weaker `is not None` check by
accident.

**Found:** CMX-338 rework round 1 (2026-09-03), judge round 1 of PR #435. `chela/main.py`'s
`cmd_telegram` builds the one production `RegistryRelay` and is the only place
`bot.send_photos` is ever wired in; no test drove `cmd_telegram`'s relay construction for this
kwarg. The judge mutated it out (`send_photos=bot.send_photos` → removed) and
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3536 passed, 0 failed, 0 errors).
Closed by `test_cmd_telegram_wires_bot_send_photos_into_the_relay` in
`tests/test_telegram_gate_starvation.py`, which spies on `chela.telegram.RegistryRelay` and
asserts the captured `send_photos` kwarg is a bound `BotSender.send_photos` method — red the
moment the kwarg is dropped, green once it's restored.
