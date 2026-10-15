## 125. A callback is pinned by identity at its call site while every test of the callee hands in a lambda, so the callback's own body never runs

**Assertion form:** a function takes a data source as a callable (`restore.boot_pass(...,
gather=...)`, where `gather()` returns `(verdicts, runs)`), and the daemon wires the real one
(`gather=main._restore_gather`). Two guards look like they cover the chain. The callee's tests
drive `boot_pass` with `gather=lambda: (verdicts, [RUN])`, so the callee's logic is exercised
against hand-built data. The wiring test spies on `boot_pass` and asserts
`k["gather"] is main._restore_gather` (here, `calls == [((NOW,), {"gather":
main._restore_gather, ...})]`). Each hop is pinned, yet no test ever CALLS `_restore_gather`:
the wiring test replaced the callee that would have invoked it, and the callee's tests never
received it.

**Mutation that defeats it:** gut the callback's body: `return verdicts,
dispatcher.list_runs()` → `return verdicts, []`. The identity check still holds (it is the
same function object), and the callee's tests never see it. In production the boot pass then
hands `resume()` no live runs, so the worktree-owner guard has nothing to match and relaunches
an old session into a live run's worktree, which is the exact hazard the PR exists to close.

**Why this is distinct from shapes 319/330/361:** those are about a leaf that tests
monkeypatch away or a DI default nobody resolves. Here nothing is patched away. The wiring
test proves the right *name* reaches the seam, and that is precisely why it reads as
coverage. Identity proves *which* function was passed, not *what it returns*.

**Guard form that survives:** call the real callback with its leaves faked (stores, `list_runs`) and
assert both what it hands its own collaborators and what it returns. Then run one end-to-end
pass with `gather=main._restore_gather` and the real `resume()` (only its tmux/filesystem leaves
faked), asserting the observable invariant: the session in the live run's worktree is not
spawned, and the one beside it is (`tests/test_restore_boot.py::
test_the_wired_boot_pass_never_relaunches_into_a_live_runs_worktree`).
