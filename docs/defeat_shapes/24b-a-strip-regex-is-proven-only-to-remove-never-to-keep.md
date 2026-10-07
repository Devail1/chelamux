## 24b. A strip regex is proven only to REMOVE its target, never to KEEP its near-misses

**Assertion form:** a new regex deletes one known noise string from relayed text (a tool's
own model-facing note, a banner, a marker). The guards feed it the exact real-world string —
lifted from the bug report — and assert it is gone, plus perhaps one unrelated sentence
("look at this") beside it that obviously survives. Every fixture is either the target itself
or text nowhere near it.

**Mutation that defeats it:** loosen the pattern — `\[Image: original \d+x\d+, displayed at
\d+x\d+\. Multiply coordinates by [\d.]+ to map to original image\.\]` → `\[Image:[^\]]*\]`,
or keep the head and replace the tail with `.*?\]`. The real note still matches, the unrelated
sentence still doesn't, the suite stays green — and the relay now silently eats any user text
that merely *looks* like the note (`[Image #1]`, `[Image: original screenshot attached]`, a
half-quoted note). A strip that over-matches deletes real content with no error, so nothing
downstream notices either.

A sibling defeat rides the same fixtures: when the strip leaves an item EMPTY, the gate that
should drop it can be swapped back to the pre-strip check (`block.strip()` instead of
`strip(block)`). With the note as the first or last item the empty string vanishes in the
final `"\n".join(...).strip()`; only a note-only item BETWEEN two real items shows the stray
blank line.

**Guard form that survives:** pair the removal test with a parametrized KEEP set of near-misses,
each differing from the target in one component (no dimensions, no "Multiply" sentence, the
sentence without the bracket, a different bracket tag, the right prefix with a wrong tail) and
assert each survives verbatim — through every content shape the parser accepts (plain string,
text block, bare string item). Put a stripped-to-empty item in the MIDDLE of real items so an
un-dropped empty one is visible.

**Found:** CMX-24 rework round 1 (2026-10-07), PR #608 — `chela/telegram/parser.py`'s
`_RE_IMAGE_COORD_NOTE`. Closed by `tests/test_telegram_read_screenshot.py::
test_parser_keeps_user_text_that_only_resembles_the_note` and
`::test_a_note_only_item_between_real_items_leaves_no_blank_line`.

**Related:** [68](68-a-sibling-regex-s-flag-or-capture-bound-drifts-from.md) is about a regex
drifting from its sibling; this one is about a regex with no negative control at all.
[49](49-a-two-sided-boolean-property-has-both-negative-halves.md) is the general
"prove both sides" principle.
