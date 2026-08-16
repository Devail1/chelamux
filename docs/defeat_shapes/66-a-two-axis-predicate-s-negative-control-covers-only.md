## 66. A two-axis predicate's negative control covers only one axis, leaving the other unpinned

**Assertion form:** a function's guard is really a conjunction of two independent conditions —
"this bullet is OPEN **and** this bullet is BLOCKED" — and the suite carries a negative control
for one axis (an open-but-unblocked bullet must not match) while treating that as coverage for
the whole predicate. Every positive fixture in the suite combines the two conditions the same
way (`[ ]` + a blocked marker), so nothing ever exercises the other negative combination
(`[x]` + a blocked marker).

**Mutation that defeats it:** widen the gate on the UNTESTED axis while leaving the tested axis's
check untouched. `m = OPEN_RE.match(raw)` → `m = OPEN_RE.match(raw) or DONE_RE.match(raw)` still
requires `BLOCKED_RE.search(title)` to pass — the tested axis's negative control (open-but-
unblocked) still correctly returns no match, because it never touches `DONE_RE` at all. The
untested axis's negative case — a bullet that is DONE and blocked — now matches, and no fixture
in the suite ever constructs one.

**Why the existing negative control doesn't catch it:** a negative control proves the specific
combination it constructs is rejected; it says nothing about combinations built along an axis it
never varied. "Open-but-unblocked is rejected" and "done-but-blocked is rejected" are different
claims about different inputs — the first tells you `BLOCKED_RE` is load-bearing, not that
`OPEN_RE` (or whatever gates the other axis) is exclusive of its sibling state.

**Guard form that survives:** for any predicate built from two independently-variable
conditions, write a negative control for EACH axis separately — one fixture that satisfies axis
A but not B, and a second that satisfies B but not A — not just one fixture that fails on
whichever axis was top of mind when the guard was written.

**Found:** `chela.sources.markdown.MarkdownSource.parked_tasks_from_text` (CMX-298, PR #372,
round 5). `tests/test_markdown_parked.py::test_a_plain_open_bullet_is_not_parked` pinned
open-but-unblocked; nothing pinned blocked-but-done. `chela judge` mutated the `OPEN_RE`-only
match to `OPEN_RE.match(raw) or DONE_RE.match(raw)` — `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
stayed green (3170 passed) with the mutation applied, because every parked fixture in the suite
combined `[ ]` with a blocked marker and none combined `[x]` with one. Closed by adding
`test_a_done_bullet_carrying_a_blocked_marker_is_not_parked`, which asserts
`parked_tasks_from_text("- [x] a done task <!-- blocked: waiting on fixtures -->\n") == []`.

**See also:** [[54|shape 54]] — a different axis-blindness (DOM presence vs. rendered
visibility) recurred on the same PR/round as this one, applied to the CSS side of the same
feature; the two are independent findings sharing only a ticket and a round.
