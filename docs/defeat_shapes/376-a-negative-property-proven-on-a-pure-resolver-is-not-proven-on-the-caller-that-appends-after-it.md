## 376. A negative property ("this flag is never present") proven on a pure resolver's return value is not proven on the real caller, which appends more flags to that return value after the call

**Assertion form:** `dispatcher.resolve_agent_cmd(wf, role)` is a pure function with a
thorough unit test asserting it never emits `--remote-control` (`--remote-control` is a
Claude Code flag reserved for windows a human sits at — the dashboard launcher and Telegram
`/new`, wired through `chela/spawn.py`'s `_add_remote_control` — never for a dispatcher-
launched agent or judge, which is an unattended worker). That test calls
`resolve_agent_cmd(...)` directly and checks the string it returns:

```python
def test_resolve_agent_cmd_never_carries_remote_control(mods, monkeypatch):
    monkeypatch.setattr(config, "REMOTE_CONTROL_ENABLED", True)
    cmd, _ = dispatcher.resolve_agent_cmd(_wf())
    assert "--remote-control" not in cmd
```

This genuinely proves the resolver itself is clean. But `resolve_agent_cmd` is never sent to
tmux on its own — its caller, `_launch_agent`, takes the returned string and keeps appending
to it: a messaging-socket arg, a sandbox arg (`chela/dispatcher.py` ~5445-5467), before the
result ever reaches `send-keys`. A test that only calls the resolver directly never reaches
any of that downstream code, so it cannot see a flag added there.

**Mutation that defeats it:** add the flag one step later, right after the resolver returns,
inside `_launch_agent` itself — not inside `resolve_agent_cmd`, and not by editing
`chela/spawn.py` (a genuinely different, already-guarded module):

```diff
-     agent_cmd, cmd_source = resolve_agent_cmd(wf, role)
+     agent_cmd, cmd_source = resolve_agent_cmd(wf, role); agent_cmd += " --remote-control x"
```

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stays green (4105 passed, 0 failed) — the resolver
unit test above still calls `resolve_agent_cmd` in isolation and still sees a clean string;
it never executes the appended line because that line lives in a different function the test
never calls. In production, every dispatched coding agent and judge would now launch carrying
`--remote-control x`, exactly the unattended-worker leak the resolver test's docstring says
cannot happen.

**Why this is distinct from [[60|shape 60]] and [[368|shape 368]]:** 60 is a shared helper's
contract proven at ONE of several call sites while a SIBLING call site bypasses the helper
entirely — the fix is mirroring fixtures onto the untested sibling. 368 is an entry point
(argparse dispatch) that is never driven AT ALL, only the pure function under it is. Here
there is exactly one call site and it IS driven — by the resolver's own unit test — but that
test stops at the function boundary; the single production caller keeps mutating the same
variable for several more lines afterward, and none of those lines are reachable from a test
that only calls the pure function. It is the same "boundary the test stops at" shape as 368,
narrowed from "the whole entry point is undriven" down to "the tail of one specific function
is undriven" — and it recurs specifically for NEGATIVE properties ("X is never present"),
because a positive property (like `--strict-mcp-config`) that the resolver DOES emit already
has a wiring-level test proving it survives to the real `send-keys` call
(`test_spawn_sends_a_strict_mcp_config_command_to_tmux` in the same file) — nobody wrote the
mirror-image negative version of that same wiring test, only the resolver-level positive one.

**Guard form that survives:** for a property proven absent on a pure resolver, also assert it
absent on the fully-wired string that reaches `send-keys` — drive the real production call
site (`dispatcher._spawn` / `dispatcher._spawn_judge`, which call `_launch_agent`
end-to-end) with `subprocess.run` mocked and the resolver-under-test's gating condition
turned ON (here, `config.REMOTE_CONTROL_ENABLED = True`, to prove the absence holds even when
the flag that turns the feature on elsewhere is enabled), and check the captured `claude …`
string the same way the existing `--strict-mcp-config` wiring tests already do for their
positive property.

**Found:** CMX-375 (PR #526), judge round on CMX-376. `chela/dispatcher.py`'s
`resolve_agent_cmd` had `test_resolve_agent_cmd_never_carries_remote_control`
(`tests/test_agent_permission_mode.py`) pinning the pure function, but nothing drove
`_launch_agent`'s real call site with the same negative assertion — the judge's own
non-blocking note on PR #526 named the gap directly ("guard (b) pins only
resolve_agent_cmd... A regression that adds the flag there is outside that test's reach").
Closed by `test_spawn_sends_no_remote_control_even_when_enabled` and
`test_spawn_judge_sends_no_remote_control_even_when_enabled`, mirroring the existing
`test_spawn_sends_a_strict_mcp_config_command_to_tmux` / `test_spawn_judge_sends_a_strict_mcp_config_command_to_tmux`
wiring pattern already in the same file, verified by re-applying the mutation above and
confirming both new tests go red while the resolver-level test stays green.
