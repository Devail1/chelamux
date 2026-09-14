## 369b. A launch-flag guard asserts the string is right but never checks the file it names was actually written

**Assertion form:** a test drives the real launch path (`_launch_agent` → `sandbox_launch_arg`)
and asserts the captured command line contains `--settings <path>`, where `<path>` is computed
by calling `sandbox.sandbox_settings_path()` a second time, independently, and string-comparing
it against what was captured. The test genuinely proves the flag names the right *location* —
but "the right location" and "a location that holds the config" are two different claims, and
the assertion only makes the first one.

**Mutation that defeats it:** `sandbox_launch_arg`'s only call is swapped from
`ensure_sandbox_settings_file()` (writes `SANDBOX_SETTINGS` to disk if it isn't already there,
then returns the path) to the bare `sandbox_settings_path()` (computes the same path, writes
nothing). Both functions return an identical `Path` for the same `CLAUDE_CONFIG_DIR` — that's
the whole trap: the string the test compares against is computed the same way on both sides of
the mutation, so `f"--settings {expected}" in claude_line` holds either way. The file the flag
now points at was never created; `--settings <path-to-nothing>` makes Claude Code's `--settings`
loader either silently skip a missing file or error, depending on version — either way the
sandbox config never applies, while the test suite — including two tests that build `expected`
via the identical `sandbox_settings_path()` call the mutation now uses internally — stays green.

**Guard form that survives:** after driving the code path that is supposed to produce the file,
assert the file's actual on-disk state, not just that a string names the right location for it:
`path.exists()` and `json.loads(path.read_text()) == SANDBOX_SETTINGS`. A guard that only checks
"the flag names path P" can never distinguish "P holds the config" from "P names a location that
happens not to exist yet" — those are different claims, and a launch-arg test for a
materialize-then-reference function must assert the materialization, not just the reference.

**Found:** CMX-369 rework round 2 (2026-09-14), PR #518. The judge applied
`ensure_sandbox_settings_file()` → `sandbox_settings_path()` in `chela/sandbox.py`'s
`sandbox_launch_arg` to a throwaway checkout of the PR head; `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q` stayed green (4059 passed, 0 failed) because every existing assertion on the
`--settings` flag compared path *strings*, never the file's existence or content. Closed by
`test_sandbox_launch_arg_materializes_the_settings_file` (new) and a strengthened
`test_launch_agent_carries_settings_flag_when_workflow_opts_in`, both in
`tests/test_sandbox_boundary.py`, asserting `path.exists()` and the on-disk JSON content after
driving the real call path — verified by re-applying the mutation by hand and confirming both go
red while every other test in the file stays green.
