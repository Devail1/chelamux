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

**Round 2 — a hand-rolled multipart body's two present pieces are glued together with no
assertion of the separator between them:** a different shape, filed here rather than as a new
file because it was found on the same CMX-338 branch and the catalog's own numbering rule ties
an added file's number to the branch's task id, which this file already claims (see
`test_defeat_shapes_added_files_are_numbered_by_branch_task_id`).

`_urllib_media_transport`'s hand-rolled `multipart/form-data` body (`chela/telegram/relay.py`)
writes each field as `f'...name="{name}"\r\n\r\n' f"{value}\r\n"` and each file part as a
header, then `parts += data`, then `parts += b"\r\n"`, before the next `--{boundary}` (or the
closing `--{boundary}--`) is appended. The one test exercising the real wire bytes
(`test_urllib_media_transport_sends_multipart_with_matching_boundary`) asserted that the
boundary line and the field's value / the file's raw bytes each appeared *somewhere* in the
body (`assert f"--{boundary}\r\n".encode() in body`, `assert b"c1" in body`, `assert
b"\x89PNGDATA" in body`) — three independent presence checks, none of which reads the bytes
*between* any two of them.

**Mutation that defeats it:** drop the trailing `\r\n` that terminates a field value or a file's
raw bytes before the next boundary:

```diff
-                 f"{value}\r\n"
+                 f"{value}"
```
```diff
-             parts += b"\r\n"
+             parts += b""
```

Both the field/file's own bytes and the following `--{boundary}` line are still present in the
body afterward — gluing them together (`c1--{boundary}` instead of `c1\r\n--{boundary}`,
`\x89PNGDATA--{boundary}--` instead of `\x89PNGDATA\r\n--{boundary}--`) changes nothing that any
existing presence check reads. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3544
passed, 0 failed) under either mutation — while a Telegram Bot API server parsing the malformed
body server-side would reject the whole upload (the `\r\n` before a boundary is not decorative;
RFC 2046 defines the boundary delimiter as starting with a CRLF, so a value that runs straight
into `--` is fused onto the boundary line as part of the value instead of terminating it).

**Guard form that survives:** for a hand-rolled wire format where a mutation could glue two
adjacent, independently-real pieces together by dropping the byte(s) between them, presence
checks on each piece in isolation are not enough — assert the exact adjacency: the piece
immediately followed by its terminator immediately followed by the next boundary, as one
contiguous substring (`b'name="chat_id"\r\n\r\nc1\r\n--' + boundary.encode() in body`,
`b"\x89PNGDATA\r\n--" + boundary.encode() + b"--\r\n" in body`), so a corruption that removes
just the separator — leaving both neighbors intact and independently "present" — breaks the
one assertion that spans the seam between them and the suite goes red.

**Round 3 — a value that has to match ACROSS two independent structures is only ever
asserted WITHIN each structure:** filed here too, same branch/task-number rule as round 2.
Judge round 2 of PR #435 found five more surviving guards in the same file; the one worth
cataloguing as its own shape (the other four are one-off missing assertions — a hardcoded
method string, a zeroed log arg, a dropped return value, a dropped header terminator —
already covered by shapes elsewhere in this catalog) is the `sendMediaGroup` field-name
pairing:

`_send_media_group` (`chela/telegram/relay.py`) builds two parallel structures that Telegram
resolves against each other at request time: a `media` JSON list whose each entry points at
`f"attach://{name}"`, and a `files` list of `(field_name, filename, data)` multipart parts.
Telegram matches an `attach://<name>` reference to the multipart part whose *field name* is
literally `<name>` — position in the list is irrelevant to the wire protocol, only the string
match is. `test_send_photos_multiple_images_calls_send_media_group_once` asserted the `media`
list's `attach://` strings (`["attach://photo0", "attach://photo1"]`) and the `files` list's
filenames (`["photo0.png", "photo1.jpg"]`) — each structure fully checked **on its own** — but
never read `files[i][0]`, the one field that has to equal the name inside the other
structure's `attach://` string for the pairing to actually work.

**Mutation that defeats it:** rename the file part's field to something that no longer matches
the `attach://` name the `media` JSON references, while leaving the filename (what the
existing assertions actually read) unchanged:

```diff
-             files.append((name, f"{name}.{self._ext_for(media_type)}", data))
+             files.append((f"attach{i}", f"{name}.{self._ext_for(media_type)}", data))
```

`media` still says `attach://photo0`; `files[0]` still has filename `photo0.png` — every
existing assertion still holds — but the actual multipart field is now named `attach0`, so
Telegram's server-side lookup of `attach://photo0` finds nothing and the whole
`sendMediaGroup` call is rejected. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
(3547 passed, 0 failed) under the mutation.

**Guard form that survives:** when two structures must agree on a shared key to work
together at runtime, assert the key **read from one structure equals the key read from the
other**, not just that each structure's own fields look individually reasonable —
`assert [f[0] for f in files] == ["photo0", "photo1"]`, the literal list of names the `media`
list's `attach://` strings reference, so renaming the field in isolation (leaving the
filename — a different field — untouched) breaks this one assertion even though every other
existing check on either structure still passes.

**Round 4 — a rejection fixture also fails a LATER, independent check in the same
OR-of-`continue` chain, so an EARLIER dead-coded discriminator is never actually what makes
the test pass:** filed here too, same branch/task-number rule as rounds 2-3. Judge round 3 of
PR #435 found two guards of this shape in a sibling file.

`_tool_result_images` (`chela/telegram/parser.py`) rejects a content block through a chain of
independent `continue`s, each gating on a different key of the same dict —
`item.get("type") != "image"`, then `source.get("type") != "base64"`, then `if not data`. Two
tests each aim at one specific link in that chain:
`test_tool_result_images_returns_none_for_text_only_content` (a `{"type": "text", "text":
...}` block, aimed at the first check) and `test_tool_result_images_skips_non_base64_source`
(a `{"type": "image", "source": {"type": "url", "url": ...}}` block, aimed at the second). But
each fixture is *minimal* — it only sets the one key the test is nominally about, leaving
every later key in the chain absent too. The text-block fixture has no `"source"` key at all,
so it is rejected by the *second* check (`isinstance(source, dict)` is `False` for `None`)
regardless of what the first check does. The url-source fixture has no `"data"` key, so it is
rejected by the *third* check (`if not data`) regardless of what the second check does. In
both cases the test's own assertion (`is None`) passes for a reason entirely unrelated to the
check it was written to pin.

**Mutation that defeats it:** dead-code the check each test claims to pin, without touching
the checks after it:

```diff
-         if not isinstance(item, dict) or item.get("type") != "image":
+         if not isinstance(item, dict) or (False and item.get("type") != "image"):
```
```diff
-         if not isinstance(source, dict) or source.get("type") != "base64":
+         if not isinstance(source, dict) or (False and source.get("type") != "base64"):
```

Under the first mutation, the text-block fixture still returns `None` — not because the type
check fired (it can't; it's dead), but because the later `isinstance(source, dict)` check
rejects the still-missing `source` key exactly as before. Under the second, the url-source
fixture still returns `None` because the later `if not data` check rejects the still-missing
`data` key exactly as before. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3578
passed, 0 failed) under either mutation — a type-checked, spec-compliant "extract only base64
image blocks" guard reduced to only ever checking that non-image content is *also* malformed
in some later, unrelated way.

**Why this is distinct from [[55|shape 55]] and [[308|shape 308]]:** those shapes are about a
downstream signal never being armed *alongside* a gating condition's refusing value, in a
`resolve`-style function whose fixtures otherwise vary independently. Here there is no
downstream signal to arm — the danger is structural: an OR-of-`continue`s chain rejects a
block through whichever check fires FIRST, and a minimal negative fixture that only sets the
one field a test claims to target will, by construction, leave every later check's own
rejecting condition ALSO true (an absent key reads as "wrong" to every later `isinstance`/
truthiness check just as readily as to the one the test named). Proving check N requires a
fixture that would pass every check *after* N, not merely a fixture that is missing.

**Guard form that survives:** for a rejection gated by check N in a chain, hand-build a
fixture that is fully well-formed for every check strictly AFTER N (present, correctly-typed
keys with valid values all the way to the end of the chain) and wrong only at check N — a
`{"type": "text", "source": {"type": "base64", "media_type": "image/png", "data": <valid
b64>}}` block for the first check (wrong `type`, otherwise a fully decodable image), and a
`{"type": "image", "source": {"type": "url", "data": <valid b64>}}` block for the second
(wrong `source.type`, otherwise a real, present, decodable `data`) — so only the check under
test can be the reason the fixture is rejected, and dead-coding it lets the block through to
production the mutant's own `_tool_result_images([block])` returning the decoded image instead
of `None`.

**Found:** CMX-338 rework round 4 (2026-09-03), judge round 3 of PR #435.
`test_tool_result_images_type_discriminator_rejects_non_image_even_with_valid_source` and
`test_tool_result_images_source_type_discriminator_rejects_non_base64_even_with_data` in
`tests/test_telegram_parser.py` close it, each building the "wrong at exactly this hop, valid
everywhere after it" fixture described above.

**Round 5a — a failure path guarded on one arm of an if/else is never re-armed on the
sibling arm that selects the same failure shape:** filed here too, same branch/task-number
rule as rounds 2-4. Judge round 4 of PR #435 found this recurring on two independent siblings.

`BotSender.send_photos` dispatches to one of two helpers depending on image count —
`_send_single_photo` for one image, `_send_media_group` for two or more — and each helper ends
with the identical shape: `if resp.get("ok"): return True` then `_log_send_drop(resp); return
False`. Round 1 had already guarded the failure path (`resp.get("ok")` False, `_log_send_drop`
called) for `_send_single_photo` via `test_send_photos_returns_false_when_upload_fails`. That
test's single-image fixture can only ever route through `_send_single_photo` — `send_photos`'s
own `len(fits) == 1` branch makes the routing unconditional — so `_send_media_group`'s own
copy of the identical `if resp.get("ok")` check, and its own `_log_send_drop` call, had no
fixture of their own: every `_send_media_group`-reaching test in the suite
(`test_send_photos_multiple_images_calls_send_media_group_once`,
`test_urllib_media_transport_sends_multipart_with_matching_boundary`,
`test_send_photos_retries_a_media_group_on_429`) scripted its transport to succeed.

**Mutation that defeats it:** widen `_send_media_group`'s ok-check to always take the success
branch (`if resp.get("ok") or True:`), or replace its `_log_send_drop(resp)` call with a no-op.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green under either — the single-image guard
that looks, at a glance, like it proves "a failed photo upload reports failure and logs it"
only ever proved that for the routing branch its own fixture happened to select image-count-1
for.

**Why the sibling's coverage doesn't transfer:** same root cause as [[311|shape 311]]
(a control proven on one object doesn't transfer to a structurally identical second one) but
one level down — here the "two objects" are two branches of a single dispatching function
selecting between two private helpers, not two public classes. A reviewer sees one thorough
`send_photos` failure test and reasonably assumes both of `send_photos`'s own internal routes
are proven, because the assertion's *name* talks about `send_photos`, not about which helper
the fixture's image count happened to route through.

**Guard form that survives:** when a function dispatches to sibling helpers by an input
property (here, `len(images)`), enumerate the helpers and confirm a failure fixture exists that
is routed to *each* one specifically — not just one fixture whose image count happens to work
for whichever helper was open when the first guard was written. Closed by
`test_send_photos_media_group_upload_failure_returns_false` and
`test_send_photos_media_group_upload_failure_logs_the_drop` (a 2-image fixture, forcing the
`_send_media_group` route) alongside a companion
`test_send_photos_single_image_upload_failure_logs_the_drop` that reads the log record the
original round-1 test never did.

**Round 5b — a type-gate feeding straight into an operation that would RAISE on the wrong
type is proven with a fixture that merely reaches a later, independently-rejecting check
instead of one that would crash without the gate:** a refinement of round 4's shape for the
case round 4's own prescribed fix ("build a fixture valid at every check after N") cannot
reach, because the checked step here is not a value-conditioned `continue` in a chain — it is
a type gate immediately followed by iteration (`for item in content`) or attribute access
(`source.get(...)`) that only *works* because of the gate.

`_tool_result_images` opens with `if not isinstance(content, list): return None` before
`for item in content:`, and later does `if not isinstance(source, dict) or source.get("type")
!= "base64":`. `test_tool_result_images_returns_none_for_string_content` fixtures `content` as
a plain string — but a string is *iterable* (into one-character strings), each of which the
next line's `isinstance(item, dict)` rejects anyway, so the function returns `None` whether or
not the first `isinstance` check ever fires. Every non-base64-source fixture in the suite
likewise used a `dict` source (just one with the wrong `"type"` value) — never a source that
isn't a `dict` at all — so `isinstance(source, dict)` was never the reason a block got skipped.

**Mutation that defeats it:** dead-code either isinstance gate —
`if False and not isinstance(content, list): return None`, or
`if (False and not isinstance(source, dict)) or source.get("type") != "base64":`.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3582 passed) under both — round 4's
own two-fixture close for this same function had no fixture at either of these two gates.

**Guard form that survives:** round 4's "well-formed after N" recipe doesn't apply when
removing check N would make the code *crash* rather than reach check N+1 cleanly — there is no
way to make an `int` "well-formed" for a `for` loop, or a bare `str` "well-formed" for `.get`.
Instead, pick a fixture of a type that is wrong in a way the *next* operation cannot tolerate
at all: a non-iterable (`12345`, not a string) for the list gate, so a dead-coded check raises
`TypeError` instead of quietly returning `None`; a value with no `.get` method (a bare `str`,
not a `dict` with a wrong key) for the dict gate, so a dead-coded check raises `AttributeError`
instead of falling through to the next `continue`. Either way the mutant doesn't just fail an
assertion, it errors — which is the point: nothing downstream can coincidentally paper over a
missing type gate feeding an operation that requires the type.

**Round 5c — both halves of a call-site seam are tested only through doubles permissive
enough to accept either calling convention, so a positional/keyword-only mismatch between the
real caller and the real callee is never pinned:** `RegistryRelay._relay_images` calls
`self._send_photos(msg.images, thread)` positionally. Every `RegistryRelay`/`TelegramRelay`
image test in the suite passes a `_PhotoStub` whose `__call__(self, images, *args)` accepts a
positional OR a keyword `thread` with no complaint either way, so it can never notice which
convention the real call site used. Every direct `BotSender.send_photos` test, in turn, always
calls it with `message_thread_id=` as a keyword. The two halves of this seam — the real
production caller and the real production callee — were each individually well-tested, just
never through each other.

**Mutation that defeats it:** make `send_photos`'s `message_thread_id` keyword-only
(`def send_photos(self, images, *, message_thread_id=None)`). Every existing test stays green
— the stub doesn't care, and the direct `BotSender` tests already used the keyword form — while
the real `RegistryRelay._relay_images` call now raises `TypeError` on every image relay in
production.

**Guard form that survives:** for a seam tested on both sides only through a double loose
enough to accept multiple calling conventions, wire the REAL callee into the REAL caller for at
least one test and drive it end to end — `RegistryRelay(stub, reg,
send_photos=sender.send_photos).on_message(...)` with a real `BotSender`, asserting the upload
actually happened with the right thread. Closed by
`test_registry_relay_wires_the_real_send_photos_positionally`.

**Found:** CMX-338 rework round 5 (2026-09-03), judge round 4 of PR #435. 5a closed by
`test_send_photos_media_group_upload_failure_returns_false`,
`test_send_photos_media_group_upload_failure_logs_the_drop`, and
`test_send_photos_single_image_upload_failure_logs_the_drop` in `tests/test_telegram_relay.py`.
5b closed by `test_tool_result_images_returns_none_for_non_iterable_content` and
`test_tool_result_images_skips_when_source_is_not_a_dict` in `tests/test_telegram_parser.py`.
5c closed by `test_registry_relay_wires_the_real_send_photos_positionally` in
`tests/test_telegram_relay.py`.
