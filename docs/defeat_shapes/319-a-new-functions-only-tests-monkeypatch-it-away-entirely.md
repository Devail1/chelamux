## 319. A new function's only tests monkeypatch it away entirely, so neither its own body nor the arguments its caller passes to it are ever exercised

**Assertion form:** a new leaf function is added (`pr_live_head_sha(pr_url, repo_dir)`, a
`gh`-backed reader) alongside a caller that consumes its result (`live_head =
dispatcher.pr_live_head_sha(pr_url, repo_dir) or row_head`). Every test that touches this
path does so via `patch.object(dispatcher, "pr_live_head_sha", return_value=...)` at the
caller's level — none constructs a fake `subprocess.run` and calls the real function
directly. This is a step beyond shape 18 (a hand-written stub that ignores the arguments
it's handed): here there is no stub body left to ignore anything — `patch.object(...,
return_value=...)` swaps out the *entire callable*, so no test path executes a single line
inside the function, and `Mock.call_args`, which silently records what the caller actually
passed, is never inspected by any assertion.

**Mutation that defeats it:** two independent mutations, either one invisible to the whole
suite. (1) Gut the function's own `return` statement — `return (data.get("headRefOid") or
"").strip() or None` → `return None` — nothing calls the real body, so nothing notices its
logic is gone. (2) Corrupt the *call site* instead — `dispatcher.pr_live_head_sha(pr_url,
repo_dir)` → `dispatcher.pr_live_head_sha(None, None)` — every test still patches the name
`pr_live_head_sha` and hands back whatever `return_value` the test chose, regardless of what
arguments the (also-replaced) callable was invoked with, so a caller that stopped passing
real arguments produces byte-identical test results.

**Why this is distinct from shape 18:** shape 18's stub is hand-written and still runs —
`lambda fmt: "12:34:56"` — it accepts real arguments and discards them, which at least
proves the call *reached* something. `patch.object(module, "name", return_value=X)` doesn't
even do that: it replaces the name in the module's namespace, so nothing under test — not
the function body, not an args-forwarding lambda — sits between the caller and the fixed
return value. The gap is one level further out: shape 18 is "the mechanism was invoked but
which request is unchecked"; this shape is "there is no mechanism left in the test at all,
on either side of the call."

**Guard form that survives:** two separate tests, matching the two places corruption can
hide. First, call the real function directly against a faked `subprocess.run` (the same
discipline shape 24 recommends for genuine external-command fakes) and assert on the exact
argv and `cwd`, and on the exact JSON key read (`headRefOid`, not `headRefName` or any other
field) — this exercises the function's own body, so mutation (1) turns it red. Second, leave
the function *unpatched* in an end-to-end test of the caller (only fake `subprocess.run`
underneath it) and assert the argv the real function built names the caller's real inputs,
not placeholders — this catches mutation (2), because a caller degraded to constant
arguments produces a *different*, observable argv (or a different observable outcome
downstream) than a caller passing the real ones.

**Found:** CMX-319 rework round 1 (2026-08-21), PR #397 — every one of the four tests
covering `pr_live_head_sha`'s caller-side precedence logic in `tests/test_judge.py`
(`test_a_STALE_pr_head_sha_column_no_longer_hides_a_dead_head` and its three siblings)
patched `dispatcher.pr_live_head_sha` wholesale via `patch.object`, so the judge's own
`chela judge self-check` mutation battery found both `return (data.get("headRefOid") or
"").strip() or None` → `return None` in `chela/dispatcher.py` and `dispatcher.pr_live_head_sha(pr_url,
repo_dir)` → `dispatcher.pr_live_head_sha(None, None)` in `chela/judge.py` survived —
3308 tests stayed green under either mutation. Closed by adding direct tests of the real
function against a faked `subprocess.run` (pinning argv, `cwd`, and the `headRefOid` key)
plus one end-to-end test that leaves the function unpatched and asserts the caller's real
`pr_url`/`repo_dir` reach it.
