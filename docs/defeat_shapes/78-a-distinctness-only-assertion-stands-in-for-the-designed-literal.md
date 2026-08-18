## 78. A distinctness-only assertion stands in for the designed literal value

**Assertion form:** two rendered outputs that are SUPPOSED to differ (two status chips, two
labels for two different inputs) are asserted only against each other —
`assert.notEqual(a, b)` — never against the specific value each one was actually designed to
produce.

**Mutation that defeats it:** replace the designed per-input value with any OTHER value that
still varies per input — e.g. render each status chip's own CSS class name as its text content
instead of its designed A/M/D/U/! glyph. The two rendered strings ('diff-status-modified' /
'diff-status-added') are still mutually distinct, so `notEqual` stays green, even though
neither one is the value a human is supposed to see. A distinctness check can tell two things
apart; it cannot tell either one is *right*.

**Guard form that survives:** assert the literal designed value for each rendered input
alongside (not instead of) the distinctness check — `assert.equal(chip(rows[0]).textContent,
'M')`, not just "not equal to the other row's text."

**Found:** CMX-299 rework round 10 (2026-08-17), PR #373. `tests/diff_modal_wiring.test.mjs`'s
file-row status chip test asserted `chip(rows[0]).textContent !== chip(rows[1]).textContent`
after already pinning each chip's class and title per row — round 2 had closed the
hardcoded-`statusMeta('modified')` mutation on the class/title, but left the chip's visible
TEXT (the entire content of a 16×16 icon-sized box) proven only distinct, not correct. The
judge rendered `${meta.cls}` instead of `${meta.label}` as the chip body; both chips still
differed from each other (`diff-status-modified` vs `diff-status-added`), so `notEqual` missed
it while `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3207 passed). Closed by
adding `assert.equal(chip(rows[0]).textContent, 'M')` / `assert.equal(chip(rows[1]).textContent,
'A')` next to the existing distinctness check.
