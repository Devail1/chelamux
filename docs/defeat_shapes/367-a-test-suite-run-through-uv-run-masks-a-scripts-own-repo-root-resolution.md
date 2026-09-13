## 367. A test suite run through `uv run` masks a script's own REPO_ROOT resolution — the ambient interpreter already has the answer the script was supposed to compute

**Assertion form:** `scripts/npm-shared-install.sh` derives `REPO_ROOT` from `$0` (`$(cd
"$(dirname "$0")/.." && pwd)`) specifically so a bare `python3 -` heredoc, invoked with a
throwaway fixture directory as cwd, can still `sys.path.insert(0, repo_root)` and `import
chela.judge`. The test suite exercises this by running the script against fixture worktrees
that never carry their own `chela/` package (`tests/test_npm_shared_install.py`), and asserts
on the script's observable behavior — whether a shared install gets reused or reinstalled.

**Why that fixture doesn't work here:** the test suite itself runs under `uv run pytest`,
which prepends this repo's own `.venv/bin` to `PATH` for every process it launches, including
the subprocess the test spawns to run the script. `chela` is installed into that `.venv` in
editable mode, so the script's bare `python3` call resolves to `.venv/bin/python3` — an
interpreter that can `import chela.judge` from *any* `sys.path[0]`, REPO_ROOT included or not.
Mutating `REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"` down to
`REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"` (pointing it at `scripts/` instead of the repo
root) should break the import and force a reinstall every run — but under `.venv/bin/python3`
the import still succeeds regardless of what REPO_ROOT computed, so every assertion the tests
made about reinstall-vs-reuse behavior still held. The ambient test environment had already
answered the question `resolves_every_declared_package` exists to ask, so the guard never
observed the mutation at all.

**Mutation that defeats it:** `REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"` →
`REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"`. Verified live: `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q tests/test_npm_shared_install.py` was 5 passed / 0 failed both before and after this
one-line change, with no other edits.

**Guard form that survives:** don't let the subprocess a test spawns inherit whatever
capability the *test's own* launcher (`uv run`, in this case) happened to add to the
environment — strip it explicitly so the subprocess is forced through the exact code path the
test claims to be checking. Here, stripping any `.venv` entry from the subprocess's `PATH`
before running the script (`tests/test_npm_shared_install.py`'s `_no_venv_env`) makes
`python3` resolve to a system interpreter with no ambient route to `chela`, so REPO_ROOT
correctness becomes the only thing that can make `import chela.judge` succeed. Re-applying the
mutation above with that env fix in place now fails
`test_symlinks_into_one_shared_install_and_reuses_it` (the shared install's sentinel file is
gone — every run reinstalls, because `resolves_every_declared_package` fails closed and the
`if` branch's `!` treats that as "not resolved").

**Found:** CMX-367 rework round 2 (2026-09-13), PR #510. The judge's required-mutation-set
verdict named the `REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"` line and reported the full
suite (`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`, 3986 tests) staying green under the
mutation. Root-caused to `uv run pytest`'s `PATH` handing the subprocess's `python3` an
editable `chela` install that doesn't depend on REPO_ROOT at all — confirmed by reproducing
the mutation locally and observing `test_symlinks_into_one_shared_install_and_reuses_it` pass
unchanged until the subprocess environment was stripped of `.venv` entries.
