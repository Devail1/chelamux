## 436. A pure label helper is unit-tested, but the DOM assignment can revert to the old inline expression

**Assertion form:** a change moves a tooltip/label computation out of the renderer into a
pure, exported helper (`stateTitle(agent, s)` in `wallmodel.js`) so it can be unit-tested,
and tests it thoroughly — the new case, the ordinary case, the null agent. The renderer's
call sites (`el.title = stateTitle(a, s)` on the Wall's state pill, `dot.title = …` on the
status dot) are edited to use it.

**Mutation that defeats it:** put a call site back to what it was before the change
(`el.title = stateTitle(a, s)` → `el.title = s.word`). The helper is still exported and
still passes every one of its own tests; the page just never shows its output. Suite green.

**Why it is not shape 352 or 412:** the helper IS imported by a test (352 is a helper no
test imports), and there is no second, still-live route to tell apart (412) — the "old
route" is a plain inline expression that no longer exists anywhere in the source, so no
source-substring check could even name it. It is shape 07's lesson with zero guarded
callers: every test reached the helper, none reached a caller.

**Guard form that survives:** render the real module in a DOM (`terminals.js` over a fake
GridStack, `renderTerminals()` from an agents cache carrying the new field) and read the
RENDERED attribute of each element the helper feeds — one assertion per call site — with
an ordinary row on the same page as the negative control (it must carry the plain word).
Re-apply the revert at each call site by hand and watch its test go red.

**Found:** CMX-436 rework round 1 (PR #587). Fixed by
`tests/wall_proxy_status_title.test.mjs` (pill AND dot, sandboxed vs ordinary).
