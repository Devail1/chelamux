## 19. A two-valued knob mounted through different mechanisms per value, so only one direction exercises the real parse

**Assertion form:** a boolean config knob has one guard per state — an OFF guard and an ON
guard — which looks like full coverage of both directions (the mirror of shape 3, which is
about a direction never mounted at all). But the two guards reach the value through different
mechanisms: the OFF guard reloads the real module against a real (cleared) env var, so it runs
the actual `os.environ.get(...)` parse; every ON guard instead does
`monkeypatch.setattr(config, "KNOB", True)` on the already-imported module, which never touches
`os.environ` or the parse expression at all.

**Mutation that defeats it:** dead-code the parse so it can never produce `True`, while leaving
the string default intact — `TERMINAL_TIMESTAMPS = os.environ.get(...)` → `TERMINAL_TIMESTAMPS =
False and os.environ.get(...)`. The OFF guard still passes (the expression still evaluates to
`False` with no env var set — dead-coding the true-producing half doesn't touch the false
default). Every ON guard still passes too, because none of them evaluate that expression at
all — they overwrite the attribute directly, downstream of the parse entirely. The knob is now
permanently OFF regardless of the env var, and nothing goes red.

**Why this is distinct from shape 3:** shape 3 is a direction that is never mounted at all — no
assertion ever reads the OFF state. Here, both directions ARE asserted; the gap is that the two
assertions don't exercise the same code. One pins the parse, the other pins something
downstream of it, and the mutation lives in the part only the first one reaches — so from a
glance at "is there an OFF test and an ON test," coverage looks symmetric when it isn't.

**Guard form that survives:** mount the ON direction the same way as the OFF direction — set
the real env var (`monkeypatch.setenv("CHELA_TERMINAL_TIMESTAMPS", "true")`) and reload the
real module, then assert the reloaded attribute is `True`. This runs the actual parse
expression in both directions, so a dead-coded half of it is caught regardless of which half.

**Found:** CMX-277 rework round 4 (2026-08-14), PR #348 — the judge's `TERMINAL_TIMESTAMPS =
os.environ.get(...)` → `TERMINAL_TIMESTAMPS = False and os.environ.get(...)` mutation survived
because every ON-state test in `tests/test_hooks.py` monkeypatched the `TERMINAL_TIMESTAMPS`
attribute directly, and the one env-reload test in the file (`test_terminal_timestamps_defaults_off_with_no_env_var_set`)
only mounted the OFF direction. Closed by adding
`test_terminal_timestamps_turns_on_with_the_env_var_set_to_true`, which reloads the real module
against a real `CHELA_TERMINAL_TIMESTAMPS=true` env var.
