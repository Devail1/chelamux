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
