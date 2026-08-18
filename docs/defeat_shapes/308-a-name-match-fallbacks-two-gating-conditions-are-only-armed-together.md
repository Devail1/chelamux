## 308. A name-match fallback's two gating conditions are only ever exercised together, never independently, while a matching live window sits ready to be wrongly claimed

**Assertion form:** a fallback matcher is guarded by two independent conditions that must
both hold before it may fire: a *status qualifier* (`row.get("status") == "claimed"`) that
scopes the fallback to one specific race window, and an *exact-equality* name check
(`lname == name`) that scopes it to the one live window that is actually this row's. Every
fixture that exercises the fallback's positive case sets both conditions to their true value
at once (`status="claimed"`, `live[wid] == name` exactly) and every fixture that exercises
its negative case removes the *downstream signal entirely* (no live fleet, or a live fleet
with no name anywhere close to a match) rather than holding the signal present and flipping
just one gating condition. So neither condition is ever proven to be doing anything: a test
that drops the status check can't tell, because the only live window on offer already fails
to exist; a test that drops the equality check can't tell, because the only name on offer is
either identical or unrelated — nothing sits in between.

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

Both mutations are invisible to the same suite for the same structural reason: the fixtures
prove the fallback fires when it *should*, and refuses when there is *nothing on offer to
wrongly fire on* — but never refuses while something wrong is on offer.

**Guard form that survives:** for each gating condition, hold the *other* condition and the
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

**Why this is distinct from [[55|shape 55]]:** shape 55 is one compound `and` gate where the
downstream tier, if reached, resolves identically whether or not the clause is checked (no
signal either way). Here there IS a downstream signal on offer in the negative fixtures for
each condition individually — the point is that no single fixture combines "signal present"
with "exactly one gating condition at its refusing value"; the two conditions are always
toggled in lockstep with the signal's own presence/absence instead of independently against
a constant signal.

**Found:** `chela/telegram/reconcile.py`'s `dispatched_window_ids`, CMX-308's spawn-time-race
fallback (PR #384, round 1). `test_a_claimed_rows_name_match_does_not_fire_without_a_live_fleet`
and `test_a_claimed_rows_name_match_does_not_claim_an_unrelated_live_window` both proved the
negative case with no matching name anywhere in `live`; nothing held a matching name present
while varying `status` or loosening the comparison. The judge applied both diffs above in a
throwaway checkout and the suite (3220 tests) stayed green under each. Closed by
`test_a_settled_row_with_no_window_id_does_not_name_match_a_live_window` (status flipped off,
matching live window still present) and
`test_a_claimed_rows_name_match_requires_an_exact_name_not_a_substring` (equality flipped to
a near-miss, matching-by-containment live window still present).
