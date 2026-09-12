## 362b. A fixture's kwarg-plumbing line is only exercised by tests that bypass the fixture entirely

**Assertion form:** `_no_live_tmux_mutation`'s `guarded_init` (`tests/conftest.py`) reads the
`env=` kwarg off the intercepted `Popen` call and forwards it into the pure classifier:
`reason = _tmux_violation(list(args), kw.get("env"))`. That one hop — pulling `env` out of a
*real* `Popen` call — is what makes the PATH-shim carve-out (`_resolves_to_real_tmux`)
env-aware at all: the carve-out only exempts a shimmed `tmux` when it's told to resolve
`PATH` against the call's *own* environment rather than the test process's.

**Why the obvious direct test doesn't catch it:** [[362|shape 362]] fixed the PATH-shim
carve-out by unit-testing `_tmux_violation` as a pure function — `_tmux_violation(["tmux",
"kill-window", "-t", "@1"], {"PATH": <shim dir>})` — deliberately choosing not to drive it
through a real `Popen`, since that's exactly the class of test that risked a live tmux call.
But calling the classifier directly also means the test constructs the `env` dict itself and
hands it straight to `_tmux_violation` — it proves the carve-out's *logic* works given some
env, without ever proving that a real `Popen(..., env=...)` call's env is what reaches that
logic in the first place. The plumbing hop (`kw.get("env")`) sits entirely outside what any
existing test exercises.

**Mutation that defeats it:** `kw.get("env")` → `None`. `_tmux_violation` then falls back to
`os.environ` for every real `Popen` call regardless of what `env=` it was actually given.
This can only WIDEN what the fence treats as the real tmux binary — a PATH-shim call made
with its own `env=` is now judged against the *test process's* PATH instead, which usually
resolves bare `tmux` to the real system binary — so the mutation is tightening-only from the
fence's own perspective (it can never let a live call through) and every existing test still
passes: the two live-`Popen` integration tests never pass a custom `env=` at all, and the
three direct classifier unit tests never go through `Popen`/`guarded_init` to begin with.

**Guard form that survives:** exercise the wiring itself — a real `subprocess.run(["tmux",
"kill-window", "-t", "@1"], env={"PATH": <dir containing a `tmux` shim that only `exit 0`s>})`
and assert it returns cleanly (no `LiveTmuxMutationEscape`, real exit code 0). Because the
call goes through the actual `Popen`-patching fixture, only the real wiring line — not a
hand-built env dict passed straight to the classifier — can make this pass; under the
mutation, `_tmux_violation` resolves `tmux` against the test process's own `PATH` (which has
the real binary on it), misjudges the shimmed call as reaching the real server, and raises.
The shim never execs anything but `exit 0`, so there is nothing here that can reach a live
tmux server either way.

**Found:** CMX-362 rework round 2 (2026-09-12), PR #495. The judge's required-mutation-set
verdict named this single surviving corruption after round 1 closed the four corruptions in
[[362|shape 362]]. Closed by driving a real `Popen` call through `guarded_init` with a shimmed
`env=`, keeping the shim itself blast-free.
