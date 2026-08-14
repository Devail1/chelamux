## 39. An index guard's own prescribed fix still leaves a second positional shortcut open: the item under test is non-zero but still the LAST one registered

**Assertion form:** shape 38 closed a single-card fixture by rendering a decoy card first and
asserting `card.dataset.kidx !== '0'` — proving the clicked card's index isn't the literal
0. With exactly two cards (decoy, then the card under test), that precondition holds: the
target's index is non-zero. The guard renders the decoy in a lane that sorts before the
target's lane (`_KANBAN_BUCKET_ORDER` / `KANBAN_LANES` in kanban.js /
kanbanlanemodel.js), clicks the target, and asserts the modal shows the target's own title.

**Mutation that defeats it:** replace the lookup's variable index with the LAST index instead
of the literal 0 (`_kanbanCardIndex[idx]` → `_kanbanCardIndex[_kanbanCardIndex.length - 1]`).
With only two cards on screen, the card under test is not just non-zero-indexed — it is also
the *most recently pushed* entry in `_kanbanCardIndex`, because it is the only card rendered
after the decoy. A "most recent" shortcut and a real `el.dataset.kidx` read resolve to the
exact same object for the one click the test ever makes. `chela judge` found this live on
CMX-290 round 2: `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3132 passed) with
this mutation applied, even though shape 38's precondition (`kidx !== '0'`) was already in
place and passing.

**Why this is distinct from shape 38:** shape 38's fix eliminated exactly one positional
default (index 0) by proving the target isn't first. It did not consider that "first" and
"last" are two *different* constant shortcuts a lookup can degrade to, and a two-item fixture
only rules out one of them — the item that isn't first is, by construction, last. Closing a
guard against one positional default can silently leave a sibling positional default
untested, even when the fix was applied exactly as the earlier shape prescribed.

**Guard form that survives:** render at least THREE items, with the item under test in the
middle — one decoy before it, one decoy after it — so the target's index is neither the
first nor the last. Assert both preconditions as setup guards: `card.dataset.kidx !== '0'`
AND `card.dataset.kidx !== String(totalCards - 1)`.

⚠️ **Correction (CMX-290 round 3):** the paragraph below, as originally written, claimed
"there is no third 'constant' position a render-order index could plausibly collapse onto"
and "no round 3 lookup-position mutation exists for this guard to miss." That was wrong —
`Math.floor(length / 2)` on the resulting three-card fixture resolves to exactly the middle
slot this fix places the target at, and it survived a real judge round. First and last are
not the only positional defaults; they were just the only two anyone had enumerated yet. See
[shape 40](40-a-fixture-position-guard-closed-by-enumeration-still.md) for why enumerating
"safe" indices can never close this class, and for the guard form (a differential assertion
across two clicks in one render) that actually does.

**Found:** `tests/kanban_task_modal_wiring.test.mjs`'s wiring guard (CMX-290, round 2)
rendered a before-decoy (`open_tasks`, Todo lane) and the card under test (`recent_runs`
status `done`, Done lane) — two cards, so the target was non-zero but still last-registered.
Closed by adding a second, after-decoy (`recent_runs` status `closed`, Archived lane, which
`KANBAN_LANES` renders after Done) and asserting the target's `data-kidx` is neither `'0'`
nor the last index among the three rendered cards.
