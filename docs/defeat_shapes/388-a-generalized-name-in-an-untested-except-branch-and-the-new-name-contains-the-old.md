## 388. A name generalized from a hardcoded literal lives in an `except` branch no test reaches — and the new name contains the old one

**Assertion form:** none, for the branch that matters. A change turns a hardcoded tool name
in an error message (`"npm is not on this machine's PATH"`) into an interpolated one
(`f"{argv[0]} is not on …"`) so the message is right for the newly supported tool (pnpm).
The tests exercise every *happy* and *non-zero-exit* path of the new tool, but the
`except FileNotFoundError:` branch that renders the name is never entered — no fixture makes
the binary missing.

**Mutation that defeats it:** revert the interpolation to the old literal
(`f"{argv[0]} …"` → `f"npm …"`). Nothing reaches the branch, so the suite stays green while a
pnpm-only machine is told to go install npm.

**Why the obvious fix is still defeatable:** a test that does reach the branch but asserts
`"npm" in problem`, or even `"pnpm" in problem` against a message that mentions pnpm elsewhere,
cannot tell the two apart — **"pnpm" contains "npm"**. When the new value is a superstring of
the old one, a bare substring check on the new value passes against the old literal's
neighbourhood, and a check on the old value passes against the new one.

**Guard form that survives:** make the binary missing (a `subprocess.run` that raises
`FileNotFoundError`) on a tree that selects the NEW tool, then anchor the whole phrase with
its left boundary — `" and pnpm is not on this machine's PATH" in problem` — and assert the
old rendering is absent (`" npm is not on" not in problem`).

**Found:** `chela/judge.py::provision_suite_env` (CMX-388 rework round 1, PR #540). Fixed by
`tests/test_judge_env.py::test_provision_names_pnpm_not_npm_when_the_installer_binary_is_missing`.
