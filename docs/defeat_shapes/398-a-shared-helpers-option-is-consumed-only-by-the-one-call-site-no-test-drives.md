## 398. A shared helper's option is consumed only by the one call site no test drives, so the option is dead to the suite

**Assertion form:** one helper takes over every call site that used to hand-roll the same
math, with an option for the one caller that differs: `placePopover(menu, anchor, { gap,
align })` (`nav.js`, CMX-398) right-aligns by default, and `align: 'center'` centres. The
jsdom suite drives the helper through its menus (`#primary-menu`, `#new-menu`) via their REAL
onclick attributes: it covers the flip above, the clamp, the scroll cap and re-placing on resize.
The changelog names a third consumer too: the touch-only tap tooltips, which flip above the
sidebar foot's CPU/RAM/Disk readouts.

**Why the obvious tests don't catch it:** every tested caller uses the default `align`. The
**only** caller that passes `align: 'center'` is the tap tooltip, whose trigger is a
`touchend` listener in an IIFE, and no test dispatched one. That makes two gaps with one cause:
- the wiring: shape [[60]], where the untested call site can go back to the old "always
  below, left-aligned" inline math and nothing fails.
- the option itself: the `align === 'center'` branch can be misspelt (`'centre'`) so that
  it never matches, and nothing fails. No tested caller can reach it, so it is dead code to
  the suite even though the helper looks thoroughly tested.

**Mutation that defeats it:**
`placePopover(tipEl, target, { gap: 6, align: 'center' })` → inline
`left = max(6, r.left); top = r.bottom + 6`, and `align === 'center'` → `align === 'centre'`.
Both stayed green (4347 passed).

**Guard form that survives:** list the helper's options. For each value other than the
default, find the call sites that pass it. If none of them is driven by a test, that value
is untested, however well the helper itself is covered. Drive that call site through its
REAL trigger (here, a `touchend` on a titled element, with `.tap-tip` sized by class
because it is created on the fly). Choose geometry where the option's value changes the
answer: an anchor 60px wide and a tip 120px wide put centred (70), right-aligned (40) and
left-aligned (100) at three different `left` values. Put the anchor at the viewport bottom so
the wiring mutation (always below) also fails.

**Found:** CMX-398, PR #550, round 1. Closed by
`tests/popover_placement.test.mjs`'s two tap-tooltip tests (foot readout ⇒ above + centred;
top anchor ⇒ below + centred). Each of the judge's two mutations turns both tests RED.
