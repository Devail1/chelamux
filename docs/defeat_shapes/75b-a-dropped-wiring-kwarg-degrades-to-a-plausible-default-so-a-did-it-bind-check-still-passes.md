## 75b. A dropped wiring kwarg degrades to a plausible default, so a "did it happen" check still passes — and the failure arm of a commit-after-success is never fed a failure

**Assertion form:** a feature is proven end-to-end at the *callee*, with the new kwarg passed
by hand (`reconcile_bindings(..., rebinds={"@9": "77"})` reopens topic 77 for the resumed
window), while the *production caller* that must thread it through — the Telegram daemon's
`_reconcile_loop`, `rebinds=sidebar_archive.pending_rebinds(...)` — is never driven. Shape
[[338]] names the undriven call site; what makes this variant easy to miss is that the
callee's **fallback is not a failure**. Without `rebinds=`, the resumed window is still an
agent with no binding, so the ordinary provision path gives it a *fresh* topic. Any test
that asserts only "the resumed window is bound" (or "a topic exists for it") stays green.

The same PR shipped a sibling: `unarchive_session` drops the archive record only after the
resume window opens (`if not result.ok: return refusal`). Every fixture used a spawn that
succeeds, so `if False and not result.ok:` — which drops the record of a session it never
resumed — survived. A third: `_run_settled` refuses a `done` run whose judge battery is
still running, but every run fixture parked `judge_state` on `""` (shape [[02]]).

**Mutation that defeats it:** `rebinds=rebinds,` → `rebinds=None,` at the caller;
`if not result.ok:` → `if False and not result.ok:`; `!= "running")` → `True)`.

**Guard form that survives:**
- Drive ONE real tick of the production loop with its inputs stubbed (a `stop` that admits
  exactly one iteration), feeding the hand-off through its real store, and assert the value
  only the wiring can produce: the OLD thread id, a `reopen` call, and **no** `create` call
  — never merely "bound".
- For commit-after-success, fake the action as **failed but otherwise well-formed** (it still
  reports a `wid`), and assert the record is kept and no side effect (session-id pin, rebind
  request) was written against the window that never opened.
- For each refusal clause in a conjunction, a fixture where that clause is the ONLY one that
  refuses (`status: "done"` + `judge_state: "running"`).
