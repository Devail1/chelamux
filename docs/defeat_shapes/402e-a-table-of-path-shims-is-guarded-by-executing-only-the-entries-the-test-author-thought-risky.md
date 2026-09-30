## 402e. A table of PATH shims is guarded by executing only the entries the test author thought risky

**Assertion form:** a builder writes a dict of `{binary name: shim body}` into a directory it
puts first on `PATH`. The test EXECUTES some of those shims and checks their behaviour (here
`tmux` pins `-L <demo socket>` and `pgrep` hides the host's services). The other entries
(`claude`, `gh`, `crontab`, `pm2`) are never read.

**Mutation that defeats it:** rename an unexecuted entry's key: `"crontab"` → `"crontab-disabled"`.
The file still parses and the test still runs its two shims. Nothing names `crontab`, so
nothing notices that the stub is gone. At runtime a missing shim does not fail. `PATH` falls
through to the REAL binary, which answers for the operator. `crontab` is per-USER, so the demo
`HOME` does not isolate it, and the Schedules view on camera shows the operator's real
crontab. The judge's round-5 experiments on CMX-402 SURVIVED this way for `crontab`, `claude`
and `gh`.

**Why this is distinct from [[402d|shape 402d]]:** that shape is about what the builder READS.
This one is about what it WRITES. A shim table fails OPEN: deleting an entry does not break
anything. It silently hands control back to the host. So each entry needs its own executed
assertion, and the set of entries has to be pinned too.

**Guard form that survives:** pin the exact set of files in the shim dir
(`{p.name for p in bin_dir.iterdir()} == DEMO_SHIMS`) AND execute every one against the
behaviour the stub promises: `crontab -l` → exit 0 with empty stdout, `gh auth status` → exit
1 with the demo message, `claude agents --json` → `[]` and anything else exit 2, `pm2 jlist`
→ `[]`.

**The same round, second shape:** the `up()` test stubbed `subprocess.run`. It recorded git's
argv and asserted the `init` and `--set-upstream-to` calls, but not `remote add origin <url>`.
So `str(origin)` → `str(REPO)` (the upstream becomes this checkout, whose path names the
operator) passed. The fix runs `make_app` with REAL git and reads the result back:
`git remote get-url origin` must be the bare clone beside the app, and `.git/config` must not
contain the repo path. Recorded argv shows what was asked. Only the resulting repo shows what
was built.

**Found:** CMX-402 rework round 5 (2026-09-30), judge review of PR #553.
`tests/test_public_media.py::_assert_stubs_answer_for_the_demo` and
`::test_demo_fleet_app_remote_is_its_own_bare_clone`. Negative controls: all four judge
mutations, plus `pm2` renamed, `CHELA_DASH_HOST` → `0.0.0.0` and `CHELA_REMOTE_CONTROL` →
`true`, each turn the suite red (`chela judge self-check`: 7/7 KILLED).
