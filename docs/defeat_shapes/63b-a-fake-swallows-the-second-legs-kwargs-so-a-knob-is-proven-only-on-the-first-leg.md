## 63b. A fake swallows the second leg's kwargs, so a knob set on both legs of a two-step call is proven only on the first

**Assertion form:** CMX-63 gave Telegram media a longer read timeout. The fetch is two network
calls in a row: `tg_media.get_file(read_timeout=MEDIA_READ_TIMEOUT)`, then
`tg_file.download_to_drive(path, read_timeout=MEDIA_READ_TIMEOUT)`. The test fake for the
first leg recorded its kwargs (`_ScriptedDoc.timeouts`), and the guard asserted them. The fake
it handed back for the second leg had the signature `download_to_drive(self, path,
**_timeouts)`, which took the kwargs and threw them away. So the test could see the knob on
`get_file` and could not see it on the download at all.

**Mutation that defeats it:** `await tg_file.download_to_drive(path,
read_timeout=MEDIA_READ_TIMEOUT)` → `await tg_file.download_to_drive(path)`. The full suite
stayed green. That leg is the one that actually timed out in the incident (the `ReadTimeout`
was on the file fetch), so the fix for the reported bug was the part with no guard.

**Guard form that survives:** a fake that accepts a kwarg the production code passes on
purpose must **record** it, not discard it. An underscore-prefixed `**_kwargs` on a fake is a
signal to check: if production code sets that kwarg deliberately, the fake is hiding it. When
one fake hands back another (a factory, a client returning a handle), pass the recorder
through so every leg writes to something the test can read. Then assert the knob on **each**
leg. Here `_FakeFile` takes a `download_timeouts` list from `_ScriptedDoc` and appends each
call's kwargs to it, and the guard checks `doc.download_timeouts` as well as `doc.timeouts`.

Found by the judge on PR #628 (CMX-63, round 1). Guard:
`tests/test_telegram_media.py::test_media_fetch_uses_longer_read_timeout`.
