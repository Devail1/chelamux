## 369. A round-trip guard compares a value against the SAME object it was derived from, so it can never disagree with itself

**Assertion form:** a "does the config get written correctly" test serializes a module-level
dict to disk, reads it back, and asserts the round-tripped value equals `module.THE_DICT` —
the exact same object the write started from (`on_disk == sandbox.SANDBOX_SETTINGS`). The
test genuinely exercises the write/read path (JSON encode, file write, file read, JSON
decode), and a bug in *that* path — wrong indent breaking the parse, a stray key added during
serialization, an encoding mismatch — would be caught. But the test was written, and reads,
as though it also pins the *content* of `THE_DICT` itself: a docstring or comment nearby
often says the dict is "frozen," "measured," or "verbatim," implying the test protects that
claim. It doesn't — the right-hand side of the comparison is downstream of the same edit as
the left-hand side, so editing the dict moves both sides together and the assertion is
identity under any such edit, exactly as query-string ordering was identity under key removal
in shape 27, or a transform fixture was a fixed point of its own transform in shape 29. Here
the "transform" is the trivial one, `x == x`, applied after a round trip that never touches
the value being claimed as frozen.

**Mutation that defeats it:** change any leaf of the source dict — flip a boolean
(`"enabled": True` -> `False`), widen or narrow a list (append a path to an `allowWrite`
list, append `"*"` to an allowed-domains list), swap a string (`"deny"` -> `"mask"`). The
write path serializes the new value, the read path deserializes it back, and the comparison
holds because both sides moved together. Applied by the judge to a throwaway checkout of PR
#518's head across three independent leaves (`sandbox.enabled`, `filesystem.allowWrite`,
`network.allowedDomains`'s `strictAllowlist`-adjacent list), `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q` stayed green each time (4058 passed, 0 failed) despite the PR's own docstring
calling the dict "Frozen to what was measured... widen it only against a fresh measurement" —
the one test shaped to enforce that sentence could not, by construction, ever see a widening.
Individually-named spot-check assertions elsewhere in the same file (`allowUnsandboxedCommands
is False`, `strictAllowlist is True`, the credentials `deny` entry) caught their own three
leaves, which is exactly why the *other* leaves — including `enabled`, the flag the whole
feature is named after — were free: piecemeal spot-checks only protect the leaves someone
thought to name.

**Guard form that survives:** for a value declared frozen/measured/verbatim, add ONE test
that compares the module's value against a literal written out independently in the test
file — never imported from, copied from, or derived from the module under test. The literal
must be typed out by hand (or generated once and then treated as fixed), so an edit to the
source dict has nothing on the other side of `==` that moves with it. Keep any existing
named spot-checks alongside it — they carry a diagnostic reason in their assertion message
(`allowUnsandboxedCommands is false or the boundary is advisory`) that a whole-dict diff
does not — but the whole-literal test is what closes the class instead of playing whack-a-mole
with whichever leaf the last rework round happened to name. State in the test's docstring
that this is deliberately a change-detector (normally a smell) precisely because the value it
pins is a frozen security boundary, so a future edit doesn't "fix" the test by loosening it
back to a round-trip comparison.

**Found:** CMX-369 rework round 1 (2026-09-14), PR #518. `chela/sandbox.py`'s
`SANDBOX_SETTINGS` dict (§6.3 of `docs/SANDBOX_BOUNDARY.md`) was pinned only by
`test_ensure_sandbox_settings_file_writes_the_measured_602_config`'s `on_disk ==
sandbox.SANDBOX_SETTINGS` round-trip and three narrow spot-checks; the judge's mutation
battery flipped `enabled` off, added `~/.claude` to `allowWrite`, and widened
`allowedDomains` with `"*"` — all three survived. Closed by
`test_sandbox_settings_dict_matches_the_frozen_602_literal_exactly` in
`tests/test_sandbox_boundary.py`, asserting `sandbox.SANDBOX_SETTINGS` against
`_EXPECTED_SANDBOX_SETTINGS`, a dict typed out independently in the test file.
