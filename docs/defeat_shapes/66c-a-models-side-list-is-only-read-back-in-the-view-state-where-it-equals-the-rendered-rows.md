## 66c. A model's side list is only read back in the view state where it equals the rendered rows

**Assertion form:** `groupSidebar` returns each group with two lists: `items` (the rows it
renders) and `archived` (the archived rows it holds, which the group menu's "Unarchive all"
reads and which keeps an all-archived group alive). Under Status *All* the two overlap; under
*Active* `archived` is the ONLY trace of the hidden rows. The one test that clicked
"Unarchive all" (`sidebar_groups.test.mjs`) first flipped the foot link to "Show archived" —
Status *All* — so it read `archived` exactly where every row in it was also on screen. The
Status test (`sidebar_view.test.mjs`) asserted only the rendered rows.

**Mutation that defeats it:** `if (isArch) g.archived.push(it);` →
`if (isArch && status !== 'active') g.archived.push(it);` (CMX-66 round 2). Under Active, a
group's archived rows vanish from its menu and an all-archived group disappears outright — so
its rows can only come back by switching Status. Every test still passed: the rendered rows
under Active were unchanged (they were hidden either way), and the one Unarchive-all test ran
under All, where the mutation is dead.

**Why it looks covered:** the side list *is* tested — the feature it drives (Unarchive all) has
a real click-through test. It is the view STATE that is parked: the test reaches the menu
through the path a human would take while looking at the archived rows, which is the one state
where the side list is redundant with what is rendered.

**Guard form that survives:** for every list a model returns beside the rendered one, assert it
in the state where it is the ONLY carrier of its data — here, Status Active: the all-archived
group stays, its menu reads "Unarchive all (N)", and the action works from there. Better still,
assert the invariant over the whole state grid (every Group-by mode × every Status: each
archived row is in exactly one group's `archived`), so no single state can be the gap.
