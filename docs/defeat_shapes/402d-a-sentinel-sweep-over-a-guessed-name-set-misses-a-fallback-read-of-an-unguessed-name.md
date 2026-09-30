## 402d. A sentinel sweep over a guessed set of names misses a fallback read of a name nobody guessed

**Assertion form:** a from-scratch env builder is guarded by [[402c|shape 402c]]'s fix. The test
plants a `probe-<KEY>` sentinel on `set(os.environ) | ALLOWED_KEYS` and asserts that no sentinel
reaches the built env. It also pins the key set exactly.

**Mutation that defeats it:** give an allowed key a value read from a DIFFERENT variable, one
that is neither set on the test machine nor on the allowlist:
`"GIT_AUTHOR_EMAIL": os.environ.get("EMAIL", "demo@example.com")`. git really reads `$EMAIL`.
No sentinel lands on `EMAIL`, so the fallback fires and the value equals the original. The key
set does not change. Mutant and original agree. The judge's round-4 experiment on CMX-402
SURVIVED this way.

**Why this is distinct from [[402c|shape 402c]]:** 402c widened the sweep from "names set here"
to "names set here plus names emitted". Both are still name lists, and the builder may read
ANY name. Each round closes one more guessed name while the class stays open.

**Guard form that survives:** swap the builder module's `os.environ` for a Mapping in which
EVERY name is set to `probe-<name>`, except the names it may legitimately inherit (PATH and
LANG here). Then any `os.environ[...]` or `.get(name, default)` of any name returns a sentinel.
Patch only the module under test (`monkeypatch.setattr(fleet, "os", namespace-with-probe-environ)`)
so the rest of the process keeps the real environment.

**Found:** CMX-402 rework round 4 (2026-09-30), judge review of PR #553.
`tests/test_public_media.py::test_demo_fleet_env_reads_no_operator_var_of_any_name`. The same
round closed a claim that was only half asserted ([[07]]). The `up()` test's comment said "the
three services, each on the demo env", but it checked the env for two of them, so
`agent-terminals.sh` could run on `env=None`. That would give it the operator's `tmux` and put
the real session on camera. The round also added a guard for `down()`'s `tmux kill-server`,
which no test reached because the fixture state carried `"env": {}`. Negative controls: both
judge mutations, `kill-server` on `env=None`, and `TERM` falling back to `$XDG_BIN_HOME` each
turn the suite red.
