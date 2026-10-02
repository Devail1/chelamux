## 430e. A rule table pins each transition's headline (status + counter), not the transition

**Assertion form:** the fix for [[430c|shape 430c]] — one table over every (status ×
non-tracker evidence) pair, each row running a real tick with the failure forced on — checks
what each transition is *named* for: the row's new status and the summary counter it bumps
(`status == "closed"`, `reconciled_closed == 1`).

**Why it doesn't guard:** a transition is more than its status flip. The same branch kills the
agent's window, frees the worktree, writes the outside-gate audit event, sets a DB flag, bumps
`merged_in_tick` (which fires `after_done`), consumes a marker file. Gate any ONE of those on
the failure flag and the row still lands on the asserted status with the asserted counter — a
failed-refresh tick that does half a transition passes the table. Adding each side effect to
each row's check is the same per-case race 430c described, one level down: every round finds
the next effect nobody listed.

**Mutation that defeats it:** (CMX-430, round 7) in the closed-PR branch
`if row["window_name"]:` → `if row["window_name"] and not summary["tracker_refresh_failed"]:`
before `_kill_window`. The row still goes `closed`, `reconciled_closed` is still 1, and the
agent's window lives on. Likewise gating `_cleanup_worktree_on_done` or
`_record_merge_outside_gate` on the flag. Suite green.

**Guard form that survives:** a DIFFERENTIAL. For each table row, run the same fixture twice —
arm A with the failure OFF in a way that triggers nothing of its own (the refresh succeeds and
reports the task still open), arm B with the failure ON — and record everything the tick does:
every table in the DB (timestamps and the per-arm tmp root normalized out), the worktree on
disk, spies on the side-effect seams (window kills, worktree cleanups, events appended), the
summary minus the failure flag itself, and — the part that catches effects nobody listed —
every call `tick()`'s own frame makes (a `sys.setprofile` hook filtered to callees whose
caller is `tick`). Assert A == B key by key. A gate on the flag, on any effect, makes them
differ. Keep one accepted case beside it: where the tracker DOES report the task closed, the
closed arm acts in full and the failed arm equals the open arm.

**Related:** [[430c|shape 430c]] (the table this upgrades); [[76|shape 76]] (a spy that captures
everything while the assertion reads one field).
