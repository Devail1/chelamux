## 93. An isolation override is tested where the default it replaces already resolves to the same path

**Assertion form:** a script must run under its own state directory, never the operator's.
It sets `HOME` **and** `CHELA_DIR` before importing chela. The test runs it in a subprocess
with every inherited `CHELA_*` variable stripped, then asserts that the runs DB and the
overrides landed under the script's own `$HOME/.chela`. Both pass.

**Mutation that defeats it:** stop setting the variable at all. Drop `CHELA_DIR=…` and keep
only `HOME=…`. The suite stays green.

**Why it looks covered:** chela's fallback for an unset `CHELA_DIR` is `$HOME/.chela`, and the
script set `HOME` to the directory whose `.chela` it also named. With the inherited value
stripped, "set it explicitly" and "forgot to, so the default kicked in" resolve to the same
path, and the files land where the test looks either way. Stripping the operator's variable
removes the exact condition the override exists to beat: an operator whose `CHELA_DIR` is
already set to somewhere else.

**Guard form that survives:** plant the hostile environment instead of scrubbing it. Give the
child an operator `HOME` and `CHELA_DIR` that point somewhere *other* than the script's
choice, and assert that nothing was written there **and** that the script's own directory
holds the state. Separately, capture `os.environ` at the moment the work starts (stub the
work function) and assert each variable the script sets or pops, with every one of them
pre-set to a hostile value. A related shape is [[02|a fixture parked on a default]]. Here the
thing parked is the environment, and the default it lands on is the override's own value.

**Found:** CMX-93 rework round 1 (2026-10-17), PR #736. The judge mutated `scripts/demo/gate.py`
`os.environ.update(HOME=str(home), CHELA_DIR=str(home / ".chela"), …)` →
`os.environ.update(HOME=str(home), …)`. Closed in `tests/test_demo_gate.py`:
`test_the_scenes_are_the_real_gates_own_output` now hands the child an operator `CHELA_DIR`
and asserts it stays empty, and
`test_main_runs_the_scenes_under_its_own_home_and_chela_dir_only` checks the environment
the scenes run under.
