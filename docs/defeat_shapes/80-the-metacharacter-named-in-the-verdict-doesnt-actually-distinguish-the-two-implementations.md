## 80. The metacharacter a verdict names as "the missing one" doesn't actually distinguish the two implementations — a different, unnamed character does

**Assertion form:** a mutation swaps one escaping call (`escHtml(wid)`) for a weaker one
(`String(wid)`) before the value is spliced into an inline `onclick` attribute. The natural
fixture — following the sibling shape ([[73|shape 73]]) where a fixture's missing `"` let
`attrEsc` silently degrade to `escHtml` — is to add a wid containing `"` (the character the
verdict's own prose names: *"a wid carrying `"` or `<` would close the onclick attribute
early"*) and assert the attribute survives intact.

**Why that fixture doesn't work here:** `escHtml` is `div.textContent = s; return
div.innerHTML`, which only encodes `&`, `<`, `>` — per `util.js`'s own comment, it "does NOT
escape quote characters that would break out of an attribute value." A wid containing `"`
breaks the attribute identically whether the call site uses `escHtml(wid)` (the real code) or
`String(wid)` (the mutation): neither escapes `"`, so both produce the same corrupted markup
and the same downstream failure. The fixture the verdict's own prose points at cannot tell the
real implementation from the mutated one — it fails (or passes) the same way under both,
because the character it's built around isn't actually what separates them. The two
implementations *do* diverge, just on a different axis: `escHtml` encodes a raw `&` to
`&amp;`, which is what stops the wid's own text from being re-interpreted as a second,
unintended HTML entity once the browser parses the emitted markup back out — `String(wid)`
skips that step entirely.

**Mutation that defeats it:** none needed beyond the one already described — the mutation
(`escHtml(wid)` → `String(wid)`) already defeats a `"`-based fixture on arrival, without any
further change, because the `"` axis was never where the two implementations parted ways.

**Guard form that survives:** verify the actual escaping helper's behavior empirically
(what characters does it encode, on THIS input, in THIS runtime) before picking the fixture
character — don't take a verdict's or a comment's prose description of what an escaping
function "does" as ground truth. Here, a wid whose raw text already reads as `&amp;` closes
the gap: `escHtml` encodes the raw `&`, so parsing the resulting markup back decodes exactly
one entity and reproduces the original wid; `String(wid)` leaves the raw `&amp;` text alone,
which the HTML parser decodes as if the wid itself had written the entity, corrupting it to a
bare `&`. Asserting the handler receives the wid back unchanged (`received === evilWid`)
catches the swap that the `"`-based fixture couldn't.

**Found:** CMX-299 rework round 9 (2026-08-17), PR #373. The judge's required-mutation-set
verdict for `chela/dashboard/static/js/terminals.js`'s Files-chip wid escaping named `"` as
the untested half of `escHtml(wid).replace(/'/g, "\\'")`, mirroring shape 73's `"`-based
fixture. Empirically checking `escHtml` under jsdom showed it never encodes `"` at all — `"`
degrades identically under both the real code and the `String(wid)` mutation, so a `"`-based
fixture would have passed under the mutation just as it does under the real code, closing
nothing. Closed instead with an `&`-based evilWid (`'@3&amp;'`) that round-trips correctly
only through `escHtml`, verified by re-applying the mutation by hand and confirming the new
test goes red while the existing `'`-based test (round 8) stays green.
