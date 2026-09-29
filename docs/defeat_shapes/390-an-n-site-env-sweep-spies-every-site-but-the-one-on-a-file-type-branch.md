## 390. An N-site env sweep spies every site but the one on a file-type branch

**Assertion form:** a sweep wires one helper (`envutil.child_env()`) into every `subprocess`
call site, and a dedicated spy file drives each site and reads the `env=` it actually passed.
The file *looks* exhaustive — one test per site, named after it — so the sweep reads as
fully pinned.

**Mutation that defeats it:** revert the helper at the one site the spy file never drove —
here `judge.parse_check`'s `node --check`, which only runs on the `.js` branch of a
suffix dispatch. `env=None` (inherit the daemon's raw env, pm2's `NODE_CHANNEL_FD` included)
stayed green across 4242 tests. The site was on the sweep's own changelog list; the spy file
was built from memory of the "big" launches (hooks, suites, tmux, provisioning), not from
the grep.

**Guard form that survives:** build the spy list from `git grep -n 'child_env()'` — every
hit is a row, and a hit with no spy test is the gap. A site gated behind a branch (a file
suffix, a flag, an error path) needs a fixture that *takes* that branch: here a `.js` file
in `tmp_path`, with `subprocess.run` faked, asserting the env `node --check` received.
Sibling of [shape 7](07-two-callers-one-guarded-the-wiring-your-fixture-happens-to.md): same
"one route covered" failure, but across a hand-enumerated sweep rather than one function's
callers.

**Found:** CMX-390 / PR #542 round 1 (2026-09-29).
