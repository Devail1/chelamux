## 32. A substring assertion pins text that survives the revert it's meant to catch

**Assertion form:** the guard asserts a literal string is present in rendered production
text (`assert "docs/DEFEAT_SHAPES.md" in body`) to prove a piece of newly-added wiring
language landed — but the asserted substring is also present, unchanged, in the wording
that existed *before* the PR.

**Mutation that defeats it:** revert the whole surrounding sentence back to its pre-PR
wording, leaving the pinned substring untouched. `docs/DEFEAT_SHAPES.md` names the same
file in both the old instruction ("add an entry" to the monolith) and the new one ("add
ONE NEW FILE to `docs/defeat_shapes/`"), so gutting the entire behavioral change — reverting
from "split into one file per shape" back to "append to the shared file" — still satisfies
the assertion. The suite stays green while the exact collision the PR exists to close comes
straight back.

**Guard form that survives:** assert on the fragment that only the NEW wording produces —
the thing that actually changed across the PR, not a filename or noun common to both
versions. Here: `docs/defeat_shapes/` (the new one-file-per-shape directory path) and the
"NEW FILE to `docs/defeat_shapes/`" phrasing that names the growth mechanism the PR
introduces.

**Found:** `chela/judge.py`'s `block_body` item 4 and `chela/dispatcher.py`'s
`REWORK_PROMPT` step 2 (CMX-284 rework round 2) —
`test_block_body_points_the_rework_agent_at_the_defeat_shapes_catalog` and
`test_rework_prompt_points_at_the_defeat_shapes_catalog` asserted only
`"docs/DEFEAT_SHAPES.md" in body`/`prompt`, a substring present in both the pre-split
"add an entry" instruction and the post-split "add ONE NEW FILE to `docs/defeat_shapes/`"
instruction. Reverting the whole sentence back to appending-to-the-monolith left both
tests green. Fixed by additionally asserting `"docs/defeat_shapes/"` and the NEW FILE
phrasing that only the current wording contains.
