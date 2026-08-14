## 49. A two-sided boolean property has both its negative halves guarded and its one positive half guarded nowhere

**Assertion form:** a single call site (`e.preventDefault()`) is meant to fire on exactly one
branch of a dispatch and stay silent on every other branch. Two guards each pin one of the
*silent* branches — dispatch the keydown when the modal is already closed and assert
`dispatchEvent`'s return value is `true` (not prevented); dispatch a non-Escape key while the
modal is open and assert the same `true`. Both are well-formed instances of [[46|shape 46]]'s
fix (read `dispatchEvent`'s own return value, not the state it gates). But the property this call
site actually has two sides to: "does NOT preventDefault on branches A and B" and "DOES
preventDefault on branch C" (Escape, while open). Guarding both negative branches makes it easy
to believe the property is covered — the two tests even sit right next to the code, one above,
one below — while the one branch where the call is supposed to fire has no assertion checking
that it fired at all.

**Mutation that defeats it:** delete `e.preventDefault();` from the open-and-Escape branch
entirely (not the gate around it — shape 46 already covers dropping the `if`). The closed-modal
test still dispatches Escape while closed, the gate correctly skips the whole block including the
now-absent call, and `dispatchEvent` still returns `true` — passes, untouched. The non-Escape
test still dispatches a non-Escape key, the key-filter still returns early before reaching the
call site, `dispatchEvent` still returns `true` — passes, untouched. The remaining test in the
file drives the ONE branch where the deleted line lived, but only ever asserted
`!isOpen()` (`hideDecisionsMenu()` still runs — it was never gated by the deleted line) and never
read `dispatchEvent`'s return value on this branch. All three tests green; Escape while the modal
is open now falls through to whatever else is listening (a browser default, a global shortcut),
exactly the behavior `e.preventDefault()` existed to prevent.

**Why this is distinct from [[46|shape 46]]:** shape 46 is one test whose *gate* can be deleted
because the checked effect is idempotent on that one branch — the fix is reading
`dispatchEvent`'s return value instead of the gated state. This shape is what's left **after**
that fix is correctly applied twice, to the two branches where the call must NOT fire. Both of
those guards are individually sound instances of shape 46's own fix. The gap isn't in either
guard's construction — it's that nobody wrote the mirror-image assertion for the one branch where
the call MUST fire, because "prove it does the right thing when active" reads as a separate,
easily-skipped task from "prove it stays out of the way when inactive," and a suite that pins
both silent cases feels — misleadingly — like it has the whole property covered.

**Guard form that survives:** when a call site has exactly one branch where an effect must fire
and one or more branches where it must not, guard the firing branch with the same
`dispatchEvent`-return-value technique, asserting the *opposite* polarity (`false` — prevented).
Do this for every side of a boolean-shaped property before considering it covered; a catalog of
guards for the branches where nothing happens is not evidence about the branch where something
does.

**Found:** CMX-292 (2026-08-14), judge review of PR #359 after all five CMX-288 rework rounds
landed. `decisions.js`'s module-scope keydown listener calls `e.preventDefault()` only when
`e.key === 'Escape'` AND the modal is open. Round 4 (shape 46) added the closed-modal guard;
round 5 added the non-Escape-while-open guard. Both read `dispatchEvent`'s return value and
assert `true`. The pre-existing `'Esc closes the open decisions modal'` test, from the original
PR, asserted only `!isOpen()`. Deleting `e.preventDefault();` from the open-and-Escape branch
left all tests — including that one — green. Closed by extending that same test to also read
`dispatchEvent`'s return value on the open-and-Escape branch and assert `false`.
