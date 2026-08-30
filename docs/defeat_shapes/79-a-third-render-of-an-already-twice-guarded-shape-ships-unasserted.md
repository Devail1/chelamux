## 79. A third render of an already-twice-guarded shape ships unasserted

**Assertion form:** a component renders the same "empty state" markup shape (a
`.diff-patch-empty` div with placeholder text) at three call sites. Two of those call sites
(a `Loading…` write at open-time and another at fetch-time) already went through a prior
rework round and got a guard, closing the *pattern's* mutation once. The third call site — the
patch pane's very first render, before any file row is ever clicked — renders the same shape
but was never independently asserted; its coverage was assumed to come along with the other
two for free.

**Mutation that defeats it:** blank the third site's text
(`<div class="diff-patch-empty">Select a file to view its diff.</div>` →
`<div class="diff-patch-empty"></div>`). Every existing assertion in the suite overwrites that
pane with a fetched patch before ever reading it (the tests click a file row and check the
patch view afterward), so the blank initial render is never observed and the suite stays green
— `grep -rn 'Select a file' tests/` returns nothing.

**Guard form that survives:** read the pane's content immediately after the modal opens, BEFORE
the first simulated file-row click — the one moment the initial-render text is actually the
pane's content and hasn't yet been overwritten by a later assertion's own setup.

**Found:** CMX-299 rework round 10 (2026-08-17), PR #373. Same test file/round as
[[78|shape 78]]. Closed by asserting `patchView.textContent.trim() === 'Select a file to view
its diff.'` right after `await flush()` on modal-open, before the row-click block that follows
it.

**Found again, a Python f-string instance of the same shape:** CMX-321 rework round 4, PR
#409. `chela/runtime_truth.py::_installed_report`'s gone-marketplace `ERROR` finding renders
`copy.marketplace` through an f-string at three sites in one message: "the installed
plugin's marketplace {copy.marketplace!r} is GONE", "`claude plugin list` reports
`chela@{copy.marketplace}`", and, inside the fix instruction itself, `` `claude plugin
marketplace add <path-or-url-to-the-{copy.marketplace}-marketplace>` ``. Round 2 pinned the
first two with phrase assertions (`"marketplace 'acme' is GONE"`, `"chela@acme"`); the third
was covered only by the bare substring `"claude plugin marketplace add" in body`, which a
blanked slug still satisfies. The judge's required-mutation-set verdict blanked exactly that
site (`{copy.marketplace}` → `{''}`) and the suite stayed green. Closed by asserting
`"path-or-url-to-the-acme-marketplace" in body` in
`test_doctor_ERRORs_when_the_marketplace_is_gone`, `tests/test_installed_plugin.py`. (The
"mirrored" `chela plugin` test, `test_chela_plugin_names_a_gone_marketplace_distinctly_from_a_stale_install`,
exercises a *different* message built independently in `chela/main.py` that never
interpolates the slug into this instruction at all — `` `claude plugin marketplace add
<path-or-url>` `` — so it was not a third render site and needed no change here.)

**Found a third time, at sibling sites in the same two messages — a different value each
time, so pinning the slug did not close it:** CMX-321 rework round 5, PR #409. Round 4's fix
above pinned `copy.marketplace` at its fourth render site in `_installed_report`'s message,
but two OTHER f-string interpolations in the very same two "gone marketplace" messages were
never pinned by any round: `chela/main.py::_report_installed_plugin`'s ⛔ line names WHICH
manifest will not load (`f"Claude Code will not load {copy.manifest} AT ALL"`), and
`chela/runtime_truth.py::_installed_report`'s detail names the registry file the verdict was
read from (`f"own registry ({hooks.plugins_dir() / 'known_marketplaces.json'})"`). Both are
sibling interpolations to ones already pinned in the same message — exactly this shape, just
a different variable each time — so a reviewer checking "is the shape 79 spot closed" for
`copy.marketplace` walked right past two more unrelated blank-able values sitting in the
same sentence. The judge's required-mutation-set verdict blanked both (`{copy.manifest}` →
`{''}`, `{hooks.plugins_dir() / 'known_marketplaces.json'}` → `{''}`) and the suite stayed
green on both. Closed by asserting the manifest path (`str(root / "hooks" / "hooks.json")`)
in `test_chela_plugin_names_a_gone_marketplace_distinctly_from_a_stale_install` and the
registry path (`str(hooks.plugins_dir() / "known_marketplaces.json")`) in
`test_doctor_ERRORs_when_the_marketplace_is_gone`, both in `tests/test_installed_plugin.py`.
**Lesson:** closing shape 79 for one interpolated value in a message does not mean the
message is closed — every distinct `{...}` render site in that message needs its own
assertion, not just the one the current round happened to name.
