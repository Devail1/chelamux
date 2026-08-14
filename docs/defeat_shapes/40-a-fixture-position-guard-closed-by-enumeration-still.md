## 40. A fixture-position guard closed by enumeration ("not first, not last") still leaves every OTHER function of the fixture's length open — the fix is a differential assertion, not a safer index

**Assertion form:** shape 39 closed a two-card fixture (decoy, then the card under test) by
adding a second decoy so the target sits neither first nor last among three cards, and
asserted both preconditions (`kidx !== '0'`, `kidx !== String(totalCards - 1)`) as guardrails.
The guard clicks the one card under test and asserts the modal shows its title.

**Mutation that defeats it:** replace the lookup's variable index with `Math.floor(idx.length
/ 2)` (`_kanbanCardIndex[idx]` → `_kanbanCardIndex[Math.floor(_kanbanCardIndex.length / 2)]`).
With exactly three cards, `floor(3/2)` is `1` — the middle slot — which is exactly where the
"neither first nor last" fix placed the card under test. The mutated code returns the exact
object the correct code would have, for the one click the test ever makes. `chela judge`
found this live on CMX-290 round 3: `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
(3133 passed) with this mutation applied, even though shape 39's two preconditions were both
already in place and passing.

**Why this is distinct from shapes 38 and 39, and why "add a smarter decoy" doesn't work
this time:** shape 38 closed index `0`; shape 39 closed index `length - 1`; both fixes worked
by finding one more constant a naive lookup could degrade to and rendering a decoy that
occupies it. That approach implicitly assumes the set of dangerous constants is small and
enumerable — shape 39's own "guard form that survives" said outright that "there is no third
'constant' position a render-order index could plausibly collapse onto." That claim is false:
`floor(length/2)`, `length-2`, and any other `f(length)` are all further constants, and a
fixture of fixed length N always has *some* index they resolve to. Placing the card under
test at index K only proves the lookup isn't hardcoded to the handful of constants someone
already thought to test against K — it can never prove no such constant exists, because the
space of possible `f(length)` formulas is unbounded. Enumerating decoys chases a moving
target; each fix narrows the *known* unsafe constants without closing the *set* of them.

**Guard form that survives:** stop trying to make the target's index unreachable by any
positional formula (impossible for a fixed-length fixture) and instead make the fixture
itself say two different things. Render at least two cards worth clicking, click BOTH in the
same render (same fixture, same length), and assert a DIFFERENT expected result for each
click. Any lookup that is a pure function of the fixture — `f(length)` — computes exactly one
value for one render, so it can match at most one of the two assertions; only a lookup that
actually reads which element was clicked (`el.dataset.kidx`, or equivalent) can satisfy both.
This closes the entire family of positional shortcuts at once, by construction, rather than
one enumerated member at a time — there is no "round 4" left to find, because no single
constant can be two different values.

**Found:** `tests/kanban_task_modal_wiring.test.mjs`'s wiring guard (CMX-290, round 3) had
already survived two rounds of "render a decoy at the unsafe index" (shapes 38, 39) when
`floor(length/2)` on the resulting three-card fixture passed identically to a real
`data-kidx` read. Closed by clicking two different cards (indices 1 and 2 of the same
three-card render) and asserting each click resolves to its OWN card's title — which kills
index `0`, `length-1`, `floor(length/2)`, `length-2`, and any other length-derived index in
one guard, without needing a fourth card or a cleverer index.
