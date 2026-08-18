## 76. A call-spy captures the full kwargs dict but the assertion narrows to one key, leaving a co-located kwarg on the exact same call unmeasured

**Assertion form:** every call through a shared helper (`_run`) is spied on by wrapping the
underlying function (`subprocess.run`) and recording something from each call — here, the full
`kwargs` dict is available at the spy site, but the assertion pulls out and checks only ONE key
(`kwargs.get("timeout")`), then discards the rest. The spy's own coverage claim
("`_run`'s single call site means every git invocation this module makes is bound") is true for
the key it checks and silently false for every other keyword argument that same call site also
hardcodes — the test's reach and the test's assertion are not the same width.

**Mutation that defeats it:** change a *different* keyword argument hardcoded at the identical
call site (`errors="replace"` → `errors="strict"`). The spy still fires — it is wrapping the
same `subprocess.run` call, at the same call site, with the same `timeout` kwarg untouched — so
`assert all(t == _GIT_TIMEOUT for t in calls)` stays green. The mutated line and the original
line are indistinguishable to a spy that only ever reads `kwargs.get("timeout")` off the
captured calls; the `errors` value was in the same dict the whole time and nothing ever looked
at it.

**Why this is distinct from [[18|shape 18]]:** shape 18 is a stub that *discards* the argument
it receives — the mechanism-was-invoked assertion is structurally blind to *any* argument,
because the fake never retains one to compare against. This shape's spy is not blind: it
captures the real, full kwargs dict from a real subprocess call and could assert on any key in
it. The gap is narrower and easier to miss because it looks like full coverage — the spy
*exists*, is wired to the real call site, and *does* check one real value from that exact call;
it just never widens the assertion to the sibling key sitting right next to the one it checks.

**Guard form that survives:** for a spy that already captures a call's full argument set, assert
on every keyword argument whose value is fixed at that call site, not just the one the test was
originally written to pin — `assert all(c.get("errors") == "replace" for c in calls)` alongside
the existing `timeout` assertion — or, if the failure mode is "would raise instead of return a
wrong value" (as here: `errors="strict"` makes a real non-UTF-8 diff raise `UnicodeDecodeError`
rather than return a corrupted string), a direct behavioral test that exercises the exact input
the hardcoded kwarg exists to handle (a file containing invalid UTF-8 bytes) and asserts the
call still returns normally is a stronger guard than reading the kwarg back at all — it proves
the *effect* the kwarg is there to produce, not just its literal presence in the call.

**Found:** CMX-299 rework round 6 (2026-08-16), judge round 5 of PR #373.
`chela/diffsurface.py`'s `_run` hardcodes `errors="replace"` alongside `timeout=_GIT_TIMEOUT` on
its one `subprocess.run` call; `tests/test_diffsurface.py`'s
`test_all_git_subprocess_calls_are_bounded_by_git_timeout` spies on `subprocess.run` and
captures every call's `kwargs.get("timeout")`, but never reads `kwargs.get("errors")` off the
same captured calls. Mutating `errors="replace"` → `errors="strict"` left the timeout spy green
(`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`, 3185 passed) — verified separately that
`errors="strict"` raises `UnicodeDecodeError` on a real latin-1 byte a git diff can legally
contain, which would 500 `/api/agents/<wid>/diff/patch` on an otherwise-legal file. Closed by a
behavioral test (`test_file_patch_handles_a_file_containing_invalid_utf8_bytes`) that edits a
tracked file to contain an invalid-UTF-8 byte and asserts `file_patch` still returns
`{"ok": True, ...}` instead of raising.
