## 308. A name-match fallback's gating conditions are only ever exercised together, never independently, while a matching live window sits ready to be wrongly claimed

**Assertion form:** a fallback matcher is guarded by several independent conditions that
must ALL hold before it may fire: an *id-absence* check (`not wid`) that scopes the fallback
to a row that has not been stamped an id yet, a *status qualifier*
(`row.get("status") == "claimed"`) that scopes it to one specific race window, and an
*exact-equality* name check (`lname == name`) that scopes it to the one live window that is
actually this row's. Every fixture that exercises the fallback's positive case sets ALL
conditions to their true value at once (`window_id=None`, `status="claimed"`,
`live[wid] == name` exactly) and every fixture that exercises its negative case removes the
*downstream signal entirely* (no live fleet, or a live fleet with no name anywhere close to
a match) rather than holding the signal present and flipping just one gating condition. So no
single condition is ever proven to be doing anything on its own: a test that drops one check
can't tell, because either the only live window on offer already fails to exist, or the other
untouched conditions still happen to agree with the downstream signal by construction.

**Mutation that defeats it:**
1. `elif not wid and row.get("status") == "claimed":` → `elif not wid:` — the status
   qualifier is dropped. A `needs_human`/`done`/`failed` row with no `window_id` (long
   settled, its window gone) now matches ANY live window sharing its old recorded name — a
   name tmux is free to hand to an unrelated human window after the row's own is reaped.
   Every existing fixture for the fallback's positive case has `status="claimed"`, so
   dropping the check changes nothing they observe.
2. `if lname == name:` → `if name in lname:` — equality is loosened to substring
   containment. A recorded name that is a *prefix* of some unrelated live window's name
   (`"cmx-30"` inside `"cmx-305"`) now claims that window too. Every existing fixture either
   has `lname == name` exactly or `lname` sharing no substring with `name` at all — nothing
   exercises a near-miss.
3. `elif not wid and row.get("status") == "claimed":` → `elif row.get("status") == "claimed":`
   — the id-absence qualifier is dropped instead of the status one. A row that DOES carry a
   `window_id`, but whose `window_epoch` is dangling (CMX-77 — a tmux restart orphaned it, so
   today's live window under that `@id` belongs to somebody else), is meant to be dropped
   outright by the epoch check in the sibling `if` branch above and never reach this `elif`
   at all. Every existing positive fixture for the fallback has `window_id=None`, so dropping
   the `not wid` clause changes nothing they observe — the row still reaches the same `elif`
   by the same `status == "claimed"` path, just now also for rows that carry a stale id.

All three mutations are invisible to the same suite for the same structural reason: the
fixtures prove the fallback fires when it *should*, and refuses when there is *nothing on
offer to wrongly fire on* — but never refuses while something wrong is on offer, and never
hold every OTHER condition (including ones not obviously "the one under test", like the id
itself) fixed at its firing value while flipping only the condition being proven.

**Guard form that survives:** for each gating condition, hold every OTHER condition and the
downstream signal fixed at the value that would make the fallback fire, and flip only the
one condition under test to its refusing value — with a live window still present that a
looser guard *would* have matched:
- status: a `needs_human` row (not `"claimed"`) with `window_id=None` and
  `window_name="cmx-305"`, against `live={"@668": "cmx-305"}` — the exact live match that
  the fallback would gladly claim if the status qualifier weren't checked — asserts the
  result is empty.
- equality: a `"claimed"` row with `window_name="cmx-30"`, against `live={"@668":
  "cmx-305"}` — a live name that *contains* the recorded name as a prefix, so a
  substring-membership check would wrongly match it — asserts the result is empty.
- id-absence: a `"claimed"` row that DOES carry `window_id="@668"` with a dangling
  `window_epoch` (an old epoch against a `now_epoch` that differs), and a recorded
  `window_name` that matches some OTHER live window exactly (`live={"@900": "cmx-305"}`) —
  the exact live match the fallback would gladly claim if the id-absence qualifier weren't
  checked — asserts the result is empty. The epoch has to actually differ (assert
  `epoch.is_dangling(...)` as a fixture sanity check), or the row would be honoured by the
  sibling `if` branch instead and the fallback's own `elif` would never even be reached.

**Why this is distinct from [[55|shape 55]]:** shape 55 is one compound `and` gate where the
downstream tier, if reached, resolves identically whether or not the clause is checked (no
signal either way). Here there IS a downstream signal on offer in the negative fixtures for
each condition individually — the point is that no single fixture combines "signal present"
with "exactly one gating condition at its refusing value"; the conditions are always toggled
in lockstep with the signal's own presence/absence instead of independently against a
constant signal.

**Found:** `chela/telegram/reconcile.py`'s `dispatched_window_ids`, CMX-308's spawn-time-race
fallback (PR #384). Round 1: `test_a_claimed_rows_name_match_does_not_fire_without_a_live_fleet`
and `test_a_claimed_rows_name_match_does_not_claim_an_unrelated_live_window` both proved the
negative case with no matching name anywhere in `live`; nothing held a matching name present
while varying `status` or loosening the comparison. The judge applied mutations 1 and 2 above
in a throwaway checkout and the suite (3220 tests) stayed green under each. Closed round 1 by
`test_a_settled_row_with_no_window_id_does_not_name_match_a_live_window` (status flipped off,
matching live window still present) and
`test_a_claimed_rows_name_match_requires_an_exact_name_not_a_substring` (equality flipped to
a near-miss, matching-by-containment live window still present). Round 2/3: closing those two
still left the THIRD condition (`not wid`) exercised at only one value — every fixture set
`window_id=None` — so mutation 3 above (dropping `not wid` while leaving `status ==
"claimed"` in place) still went green under the same suite (3223 tests). Closed by
`test_a_claimed_rows_stale_window_id_does_not_fall_through_to_name_match` (id-absence flipped
off via a dangling-epoch row that DOES carry a `window_id`, matching live window under a
DIFFERENT id still present).
