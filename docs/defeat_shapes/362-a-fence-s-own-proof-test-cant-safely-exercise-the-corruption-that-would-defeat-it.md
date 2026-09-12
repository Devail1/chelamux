## 362. A fence's own proof test can't safely exercise the corruption that would defeat it, so its internals ship with zero direct coverage

**Assertion form:** a suite-wide `subprocess.Popen`-patching fence (`_no_live_tmux_mutation`
in `tests/conftest.py`) is proven by exactly one end-to-end integration test: it really calls
`subprocess.run(["tmux", "kill-window", "-t", "@1"])` and asserts the fence's escape class is
raised before the real tmux binary ever runs. A counterweight test proves the fence isn't a
blanket blocker by really calling a `-L`-scoped invocation and asserting it does NOT raise.
Both pass; the fence "looks" proven end-to-end, in both directions.

**Why the obvious extra fixtures don't exist, and why that's not an oversight:** the fence's
whole job is to intercept a mutating tmux call before it reaches the operator's live default
socket. Driving *more* corruptions of the detection half (removing `kill-window` from the
mutating-subcommand set, dead-coding the `raise`, flipping the fixture to `autouse=False`)
through the SAME real-`subprocess.run` shape used to prove the fence works would not just turn
the suite red — under the corruption, the call it issues (`tmux kill-window -t @1`) would
actually reach the operator's default socket, where `@1` is a plausible LIVE window id. The
test built to prove the fix would refire the exact incident (issue #494) the fix exists to
prevent. So the safe, obvious integration-style extra coverage was deliberately left out —
and every internal branch below the fence's single proven entry point (the escape class's
exception hierarchy, the `-L`/`-S` exemption, the PATH-shim carve-out, the exact membership of
the mutating-subcommand set) shipped with no test of its own at all.

**Mutation that defeats it:** four independent corruptions, none caught:
1. `class LiveTmuxMutationEscape(BaseException)` → `(Exception)` — a call site that wraps its
   tmux call in `except Exception` (the shape `chela.spawn._send` and
   `chela.agent_manager.reconcile_window_names` use around their own tmux calls, for the same
   "a hiccup must never stall a live agent" reason as the sibling `LiveStateEscape`) can now
   swallow the fence silently.
2. `if "-L" in rest or "-S" in rest:` → `if False and (...)`  — the private-socket exemption
   is dead. Undetected because the ONE test of this exemption puts `-L` at `rest[0]` (real
   tmux argv order: the socket flag precedes the subcommand), where the *subcommand-membership*
   check alone already returns `None` for it — the exemption line the test is supposedly
   proving is never actually the reason that test passes, mutated or not.
3. `os.path.basename(prog) != "tmux" or not _resolves_to_real_tmux(prog, env):` → drop the
   `_resolves_to_real_tmux` half — a PATH-shim script merely named `tmux` (the shape
   `tests/test_terminals_selfheal.py` itself uses) now gets policed as if it were the real
   binary, and nothing catches the exemption disappearing because nothing calls the classifier
   with a shim on `PATH` directly.
4. Drop `"send-keys"` from `_TMUX_MUTATING_SUBCOMMANDS` — the subcommand that types into a
   live agent's pane silently stops being policed. Undetected because only `kill-window` (the
   one subcommand the single integration test happens to use) has any coverage at all; the
   other seven declared members are asserted only by being typed into the source once.

**Guard form that survives:** unit-test the PURE classifier (`_tmux_violation`) directly,
with no `subprocess.Popen` in the loop at all — this is what makes it safe to cover the
corruption-adjacent branches without ever risking a live call:
- A synthetic writer that wraps a real `subprocess.run(["tmux", "send-keys", ...])` in its own
  `except Exception: return` and asserts the fence's exception still propagates PAST that
  handler — proving `BaseException`, not `Exception`, without depending on today's (narrower)
  real call sites still using `except Exception` around tmux by the time this reads.
- Call `_tmux_violation(["tmux", "kill-window", "-t", "@1", "-L", "x"], None)` directly — a
  shape where the mutating subcommand sits at `rest[0]` and `-L` appears LATER in `rest` — so
  only the explicit exemption line, not subcommand membership, can be why this returns `None`.
- Call `_tmux_violation(["tmux", "kill-window", "-t", "@1"], {"PATH": <dir with a fake tmux
  script>})` and assert `None` — exercising the PATH-shim carve-out without ever touching a
  real `Popen`.
- Parametrize over every declared member of `_TMUX_MUTATING_SUBCOMMANDS` and assert
  `_tmux_violation` flags each one on the default socket (cf. shape/`test_
  every_door_into_the_real_dir_is_guarded`'s door-by-door parametrization) — a hardcoded
  expected list, not one derived from the source set, so dropping (or silently adding) a
  member fails a specific, named case instead of nothing.

**Found:** CMX-362 rework round 1 (2026-09-12), PR #495. The judge's required-mutation-set
verdict named all four corruptions above surviving `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
(3952 passed) on the original branch, and its own non-blocking notes explained why the
obvious integration-shaped fixtures for the detection half were deliberately absent — driving
them for real would fire the exact live-window-kill incident the fence exists to prevent.
Closed by testing `_tmux_violation` as a pure function for every branch above, keeping the
suite's only two live-`Popen` tmux-fence tests exactly as narrow as they already were.
