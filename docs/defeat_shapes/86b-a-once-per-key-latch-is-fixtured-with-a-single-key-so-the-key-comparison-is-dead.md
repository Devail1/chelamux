## 86b. A once-per-key latch is fixtured with a single key, so the key comparison is dead

**Assertion form:** a notice is edge-triggered "once per (run, head)" / "once per hold" / "once
per quiet spell" / "once per announced status". The test fires it, ticks again with the SAME
input, and asserts no repeat — then stops. Every fixture uses one head sha, one hold, one
announced status, one activity stamp.

**Why that's a gap:** with one key value in play, "once per key" and "once EVER" are the same
function. The comparison that makes the latch per-key (`seen == sha`, `entry["head"] != head`,
`release["created_at"] == hold["created_at"]`, `announced["status"] == status`) can be dropped —
or collapsed to a truthiness check — and every assertion still holds, because no test ever
presents a SECOND key that the latch must treat as new (or a stale record from an OLD key that
it must refuse to match).

**Mutation that defeats it:** `if seen.get(task) == sha:` → `if seen.get(task):`;
`entry.get("head") != head` → dropped; the release record's `created_at` match → dropped; the
echo's `announced["status"] == status` → dropped. Suite green — while in production the agent's
next push going red is never announced, an old resume record silences a later TTL lapse, and an
orchestrator's announced `close` swallows the `needs_human` the run actually went to.

**Guard form that survives:** for every latch, feed it at least TWO keys: the same key again
(must stay quiet) AND a different key (must fire — and, where the latch carries a clock, must
start the clock over). For a record matched against the thing it describes, feed a record that
describes a DIFFERENT instance and assert it is not taken as a match. For a membership tuple
(`REOPEN_FROM`, `OPEN_RUN_STATES`), parametrize over every member AND at least one non-member,
with the literals spelled out in the test rather than read from the constant.
Related: [[86|shape 86]], [[13|shape 13]], [[306|shape 306]].

**Found:** CMX-86 rework round 2 (2026-10-12), PR #655 — a held-out experiment survived; a
local battery of ~40 corruptions over the PR then showed ~25 more surviving, most of them this
shape (`ci_red`'s per-sha latch, `judge_wait_events`' per-head reset, `hold_events`' release
match and re-take, `run_events`' echo status match, `conflict_events`' settled-run filter,
`stuck_events`' started-at stamp). Closed in `tests/test_inbox_cmx86.py` by
`test_a_red_on_a_NEW_head_is_a_new_notice`, `test_judge_not_started_a_new_head_is_a_new_wait`,
`test_a_stale_release_record_does_not_explain_a_later_lapse`,
`test_a_re_taken_hold_is_the_one_the_release_is_matched_against`,
`test_an_announcement_for_another_status_does_not_silence_this_edge` and siblings.
