## 303. A test's chosen argument value coincides with the value a hardcode would produce anyway, so ignoring the argument is invisible

**Assertion form:** a function takes a parameter (here, `port`) that is supposed to be
threaded into a rendered string, and the test proving that calls the function with the SAME
value the code's own default/fallback constant already carries — `hooks.message_display_command(port=5001)`
asserting `"http://127.0.0.1:5001/hooks/MessageDisplay" in command`, where `5001` is also
`config.DEFAULT_DASHBOARD_PORT`, the value `hook_url()` falls back to when no port is passed
at all.

**Mutation that defeats it:** stop reading the `port` parameter and hardcode the fallback
value at the one call site that used to honor it:

```diff
-             f"{hook_url('MessageDisplay', port, host)} 2>/dev/null || true")
+             f"{hook_url('MessageDisplay', 5001, host)} 2>/dev/null || true")
```

The rendered command still contains `http://127.0.0.1:5001/hooks/MessageDisplay` — not
because the argument was honored, but because the test's chosen input and the hardcode's
output happen to be the same digits. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
(3212 passed) with the corruption in place. In production this is the exact CMX-41 shape one
transport over: a dashboard published on a non-default port renders a manifest whose
`MessageDisplay` curl still targets 5001, a port nothing is listening on.

**Why this slips through even though the test looks like it exercises the parameter:** the
call *reads* as "prove the port argument is plumbed through" — it passes a port and asserts
that port appears in the output — but nothing about the test distinguishes "the argument was
used" from "the argument happened to equal what the code does regardless of the argument."
A reviewer scanning the assertion for the literal `5001` sees the value they'd expect either
way, so the test looks like coverage without being coverage. This repo already had the
correct pattern next to it, for the *sibling* command hook: `tests/test_config_env.py`'s
`test_an_explicit_port_still_renders` renders `SessionStart`'s curl at `port=6001` — a value
that is NOT `config.DEFAULT_DASHBOARD_PORT` — specifically so a hardcoded fallback and a
plumbed-through argument produce visibly different output. The new `MessageDisplay` guard
was written independently and didn't reuse that precedent.

**Guard form that survives:** choose a test value that differs from every fallback/default
the function could silently substitute for it — never the value the parameter would already
equal if the code ignored it entirely. Before trusting a "does this argument get plumbed
through" assertion, ask what the function would render if it dropped the argument on the
floor; if the test's chosen value can't tell the two apart, it isn't testing plumbing, it's
testing that the digits `5001` appear somewhere in a string that was always going to contain
them.

**Found:** `tests/test_hooks.py::test_message_display_command_relays_into_the_same_http_endpoint`
(CMX-303, PR #377, rework round 1) — the mutation above, applied by the judge to a throwaway
checkout of the PR's head, stayed green. Closed by moving the test's port to `6001` (matching
`test_an_explicit_port_still_renders`'s existing convention for the sibling `SessionStart`
command hook) and extending that same sibling test to cover `MessageDisplay`'s command too,
so both of this repo's command hooks share one port-plumbing guard instead of the new one
inventing its own, weaker version.

**Also found (CMX-351, PR #464, rework round 1, unrelated ticket — same underlying shape,
at THREE call sites at once):** `chela/telegram/panescan.py`'s `detect_status` reads
`manifest.spinners` and `manifest.active_marker`, and `detect_exitplanmode` reads
`plan_pattern.top`, from the manifest loaded via `detection_manifest.load()` — but the bundled
manifest's own values (`spinners = "·✻✽✶✳✢"`, `active_marker = "…"`, the `"ExitPlanMode"`
pattern's `top` regexes matching "Would you like to proceed?") are exactly the literals the
module used to hardcode before this PR's extraction. The judge replaced each read with a
hardcoded copy of that same bundled value — `spinners = frozenset("·✻✽✶✳✢")`,
`active_marker = "…"`, and the `top` search inlined as
`(re.compile(r"^\s*Would you like to proceed\?"), re.compile(r"^\s*Claude has written up a
plan"))` — and every existing test, all of which exercise only the bundled manifest with no
override present, stayed green (`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`, 3774 passed) under
all three mutations: a hardcoded copy of today's default is byte-identical to reading it, for
any pane the bundled-manifest fixtures ever construct. Closed by three new tests, each writing
an override manifest whose value for that ONE field differs from the bundled default (a `"#"`
spinner, an `"LIVE"` active_marker, a `"CUSTOM_PROCEED_MARKER"` top regex) and asserting the
pane using the OVERRIDE's value classifies correctly while the pane using the BUNDLED default's
value no longer does — the exact "differs from every fallback the code could silently
substitute" fix this shape's guard form already prescribes, applied to a module-level accessor
read instead of a function parameter.
