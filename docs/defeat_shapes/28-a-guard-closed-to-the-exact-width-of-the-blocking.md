## 28. A guard closed to the exact width of the blocking finding, leaving a named remainder undefended

**Assertion form:** a judge round's blocking finding names a gap and prescribes a fix that
covers MORE ground than the finding strictly requires — e.g. a non-blocking note beside the
finding says "one fixture covering A, B and C would close this" — and the rework closes only
the narrowest slice that makes the blocking finding itself go away (A), leaving B and C
exactly where the note found them. The suite goes green, the round passes, and — because
non-blocking notes cost no round and block nothing — the fact that B and C are still
unguarded carries **no signal** into the next round. It reads as closed until a future judge
round independently re-derives B or C from scratch.

**Mutation that defeats it:** corrupt B or C. Nothing added by the "fix" reaches either one,
so the corruption ships clean — while the PR now claims (via the closed finding) that the
whole area is guarded.

**Guard form that survives:** when a judge note prescribes a fix wider than the blocking
finding strictly requires, close the WHOLE prescription in the same round, not just the part
that makes the round pass — the marginal cost of the rest is usually small (it is often one
extra fixture row, not a new file) and a note that named the gap and was only partially acted
on is exactly the shape the next round is built to find.

**Found:** CMX-279 rework round 3 (2026-08-14), PR #350. Round 2's non-blocking note named
three unguarded `knInline`/`knLink` rules — bold spans, links, and the two `knInline(
displayTitle(...))` call sites in kanban.js/taskmodal.js — and prescribed "one fixture ...
covering a bullet run, a bold span and an .md link would close all three at once." The round-2
rework took only the bullet run (closing DEFEAT_SHAPES #18, the blocking finding) and left
bold/links/call-sites exactly where the note found them. Round 3's judge re-derived all three
as blocking mutations. Closed by extending `tests/taskmodal_model.test.mjs`'s knMd fixture to
cover a bold span, an external link, an in-bundle `.md` link and a `#anchor` link in one
assertion, plus two independent DOM-level wiring tests (`tests/kanban_flatten.test.mjs` and
the new `tests/taskmodal_render.test.mjs`) driving each `knInline(displayTitle(...))` call
site through its real caller.

**Recurred:** CMX-279 rework round 4 (2026-08-14), same PR #350, same underlying note — it had
named FOUR gaps (blockquote, fenced code, plus the two already covered above), and round 3
only closed the two it was blocking on. The blockquote and fenced-code branches were still
byte-identical to where round 2 found them; round 4's judge re-derived both as blocking
mutations a second time, plus two more the note never explicitly named (knInline's own
`escHtml` call, and `attrEsc` on knLink's href — both real behaviour the PR's rewritten code
carries, just never exercised by a fixture with an HTML-special character or a quoted href).
Closed by three more assertions in the same `tests/taskmodal_model.test.mjs` (blockquote+fence
in one fixture, escHtml, attrEsc-on-quote) plus the third `knMd` call site as a fourth
DEFEAT_SHAPES #7 wiring test (see above). The standing lesson: when a note names N gaps and a
blocking finding only forces closing a subset, close ALL N in the same round — a partial close
does not make the round's own note stop being a to-do list for the next judge.
