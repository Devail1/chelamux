## 369c. A launch guard pins the RETURN VALUE on both no-op paths but never the SIDE EFFECT riding along with it

**Assertion form:** a function has two early-return no-op paths (`role == "judge"` and
`not sandbox_enabled(wf)`) followed by one call with a side effect
(`ensure_sandbox_settings_file()`, which writes a file to disk) before the real return.
Tests for both no-op paths assert only the return value — `sandbox_launch_arg(wf, "judge")
is None`, `sandbox_launch_arg(wf, "coding") is None` when not opted in` — because the
return value is the thing the function's name and docstring are about. Neither test looks
at whether anything on disk changed as a result of the call.

**Mutation that defeats it:** hoist the side-effecting call (`path =
ensure_sandbox_settings_file()`) above both early-return checks instead of leaving it
after them. Every return value on every path is untouched by construction — the judge
still gets `None`, the not-opted-in workflow still gets `None`, the opted-in coding role
still gets the same `f"--settings {path}"` string — so every assertion in the test file
that reads a return value continues to hold. What changed is invisible to all of them: the
two paths that are supposed to be pure no-ops now write `chela-sandbox.settings.json` to
disk on every call, including into the real `~/.claude` of whichever machine runs an
unmodified test (a second, independently dangerous consequence of the same gap — see the
CLAUDE_CONFIG_DIR note in PR #518's round-3 review).

**Guard form that survives:** for a function whose contract explicitly promises "launches
byte-identically to today" or "no side effect" on a given path, assert the ABSENCE of the
side effect directly on that path, not just the return value: set the isolating env var
(here `CLAUDE_CONFIG_DIR` to a tmp dir) and assert
`sandbox.sandbox_settings_path().exists() is False` after calling the no-op path. A return
value and a side effect are different claims about the same call; a reorder mutation that
preserves every return value can still violate the side-effect-free promise, and only an
assertion aimed at the side effect itself can see it move.

**Found:** CMX-369 rework round 3 (2026-09-14), PR #518. The judge applied — to a throwaway
checkout of the PR head — hoisting `ensure_sandbox_settings_file()` above
`sandbox_launch_arg`'s two early `return None`s in `chela/sandbox.py`;
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (4060 passed, 0 failed) because
`test_sandbox_disabled_by_default` and
`test_sandbox_launch_arg_never_fires_for_the_judge_even_when_enabled` in
`tests/test_sandbox_boundary.py` only ever checked the return value on those two paths.
Closed by adding `assert not sandbox.sandbox_settings_path().exists()` (with
`CLAUDE_CONFIG_DIR` set to an isolated tmp dir) to both tests, plus the same check in
`test_launch_agent_omits_settings_flag_when_workflow_has_not_opted_in` — verified by
re-applying the mutation by hand and confirming all three go red while the rest of the
file stays green.
