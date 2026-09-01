## 328. A boundary widened for a latched sentinel's sake, and the loop variable that carries the sentinel across calls, both go unexercised

**Assertion form:** a function introduces a negative sentinel (`UNKNOWN_BEHIND = -1`) to
distinguish "already notified about an unknowable state" from a real, never-negative count,
and threads it through a caller-held accumulator (`previously_behind`) that the daemon loop
re-passes on every tick. Landing the sentinel required widening an existing edge condition
from an exact match to an inequality — `previously_behind == 0` (the pre-sentinel invariant:
"only notify once, coming from a state that was never behind") became `previously_behind <=
0` (needed so a checkout latched at -1 can still notify once it later becomes genuinely
behind) — and the caller that stores the sentinel needed its assignment kept intact
(`update_behind_seen = update.check_and_notify(update_behind_seen)`) so the value actually
persists tick-to-tick instead of being recomputed from a stale seed. Every existing test of
the seam enters `previously_behind` already at its narrowed value (0), and every existing
test of the call-site drives only ONE tick — so neither the widened half of the boundary nor
the loop variable's round-trip is ever the thing under test, even though both are separately
mutable and separately load-bearing.

**Mutation that defeats it:** two independent one-line reverts, both green under the full
suite before this fix: (1) narrow `previously_behind <= 0` back to `== 0` — every "unknown,
then behind" sequence a human might reasonably picture (no upstream configured yet → gains
one → falls behind) then goes permanently silent, since `-1` never equals `0`; nothing tests
entering the real-update edge FROM the sentinel, only from the ordinary post-notify `0`. (2)
drop the call-site's assignment, calling the seam as a bare statement — the daemon now
re-derives `previously_behind` from whatever the loop's own initial value was on every tick
instead of what the seam last returned, so the "notify once" contract dies in production
while every unit test of the seam itself (which supplies its own `previously_behind` by hand,
never through the loop) stays green.

**Why one shape covers two mutations:** they are the same missing test from two altitudes.
The seam-level test suite validates `check_and_notify`'s *logic* by calling it directly with
hand-picked `previously_behind` values, which is correct for testing the logic but never
proves the logic's own contract (its second return value becomes the NEXT call's first
argument) actually happens across real ticks of the thing that calls it. A round-trip that
only the wiring can break is invisible to a suite that only ever unit-tests the seam and only
ever drives the loop for a single tick — you need a test that (a) enters the sentinel branch
first and later crosses into the widened boundary in the SAME sequence, and (b) drives the
production loop for at least two ticks and asserts what value tick 2 actually received.

**Guard form that survives:** for the boundary widening — construct the exact sequence the
widening exists for (unknown-state tick, THEN gain a real update) using the real seam, and
assert the real-update notification fires on the second tick, not just that the sentinel is
returned from the first. For the wiring — drive the production loop (not the seam directly)
for two ticks with the interval guard forced due on both (e.g. pin the interval constant to
`0` so the real wall-clock gap between ticks still satisfies it), stub the seam to return a
value distinguishable from the loop's initial seed, and assert tick 2 is called with tick 1's
*return* value, not the seed — a dropped assignment reverts every tick to the seed and this
goes red; a working `x = f(x)` passes.

**Found:** CMX-328 rework round 1, PR #420. The judge's required-mutation-set verdict found
`chela/update.py`'s `previously_behind <= 0` narrowable to `== 0` and `chela/main.py`'s
`update_behind_seen = update.check_and_notify(update_behind_seen)` reducible to a bare
`update.check_and_notify(update_behind_seen)` call — both with the full suite (3458 tests)
staying green. Closed by adding
`test_notifier_fires_when_a_checkout_that_was_unknown_later_falls_genuinely_behind` (drives
the real seam through an unknown-then-behind sequence and asserts both notifications fire) and
`test_the_daemon_loop_carries_check_and_notifys_return_value_into_the_next_tick` (drives
`main.cmd_run` for two real ticks with `UPDATE_CHECK_INTERVAL_SECONDS` pinned to 0 and asserts
tick 2 receives tick 1's stubbed return value rather than the loop's initial `0` seed); both
verified to fail when each mutation above is re-applied by hand.

### Round 2 addendum: a new branch's `notify.enabled()` gate went untested because every fixture for it hardcoded the notifier as enabled

**Assertion form:** the same sentinel branch has an existing, correctly-written
`if notify.enabled(): notify.send(...)` gate, copied from a sibling branch (the ordinary
"behind" branch) that already has a dedicated disabled-notifier test
(`test_notifier_never_pulls`, constructing `_StubNotify(enabled=False)`). Every test written
for the *new* sentinel branch — including the round-1 test added specifically to close the
payload-assertion gap above — constructs `_StubNotify(enabled=True)`, because those tests
were designed to prove the branch's content (log line, push payload, edge-triggering), not to
re-prove the gate on a second call site that only looks like it inherited proof from its
sibling.

**Mutation that defeats it:** replace `if notify.enabled():` with `if True:` immediately
before the sentinel branch's `notify.send(...)` call. Every test for that branch still
constructs an enabled stub, so nothing ever observes `stub.sent` while disabled — full suite
(3470 tests) stays green.

**Why this is the same family as the mutations above, not a coincidence:** all three
mutations exploit the same gap — a new branch reuses a shape (a boundary check, an
accumulator round-trip, a notifier gate) that already has a proof *somewhere else in the
file*, and every test written for the new branch inherits that shape's apparent coverage
without re-deriving it at the new call site. "The sibling branch tests this" is not evidence
that the new branch does.

**Guard form that survives:** construct `_StubNotify(enabled=False)`, drive the sentinel
branch specifically, and assert `stub.sent == []` — mirroring the disabled-notifier proof
already used for the sibling branch, rather than assuming it transfers.

**Found:** CMX-328 rework round 2, PR #420. The judge's required-mutation-set verdict found
`chela/update.py`'s sentinel-branch `if notify.enabled():` reducible to `if True:`, with the
full suite (3470 tests) staying green — every prior test of that branch used an enabled stub.
Closed by adding `test_notifier_respects_disabled_notify_on_transition_to_unknown_state`
(mirrors `test_notifier_never_pulls` for the sentinel branch, asserting `stub.sent == []` when
disabled); verified to fail when the mutation above is re-applied by hand.

### Round 3 addendum: the round-2 fix cited a sibling test as prior proof without checking the sibling test actually asserted anything

**Assertion form:** round 2's fix (above) justified itself by pointing at
`test_notifier_never_pulls` as an existing, working proof that the *sibling* ("behind")
branch's `if notify.enabled():` gate was already guarded — both in this file's round-2
addendum ("a dedicated disabled-notifier test... constructing `_StubNotify(enabled=False)`")
and in the new test's own docstring ("proves the pre-existing `behind` branch respects a
disabled notifier"). Neither claim was checked against what the cited test actually asserts:
`test_notifier_never_pulls` constructed `_StubNotify(enabled=False)` and passed it straight
into `monkeypatch.setattr` **without binding it to a name**, so the test had no handle to
read `.sent` off of — it asserted `behind == 1`, that no `pull`/`merge` git call happened, and
that HEAD never moved, but never that the notifier stayed silent. The disabled notifier was
scenery in that test, not the thing under test. A citation ("X already proves Y") was taken as
fact by two separate pieces of prose without either one re-deriving it, so the false claim
propagated from the test docstring into the catalog entry itself — the catalog became a
second, durable place carrying the same wrong belief.

**Mutation that defeats it:** identical to round 2's mutation, replayed against the *sibling*
branch instead of the new one — replace the pre-existing `if notify.enabled():` guarding
`notify.send(f"{status.behind} commit(s) behind...")` in the ordinary "behind" branch with
`if True:`. Full suite (3471 tests) stays green, because the only disabled-notifier test that
exercises that branch never reads `stub.sent`.

**Why this is a distinct pattern from the round-1/round-2 family above:** those shapes are
about a *new* branch silently inheriting a *real* sibling proof it never re-derived. This one
is about a proof that was never real in the first place — a test built the right fixture
(`_StubNotify(enabled=False)`) but discarded the handle needed to assert on it, and two
independent pieces of prose (a docstring, a catalog entry) both cited it as settled fact
without opening the cited test to check. Trusting a citation is cheaper than re-deriving the
proof, which is exactly why it is dangerous: the next reader has even less reason to doubt it,
since now *two* sources agree.

**Guard form that survives:** never cite a test as proof of an invariant without opening it
and confirming it asserts that invariant on a bound value. For a disabled-notifier fixture
specifically: always bind it to a name and assert `stub.sent == []` — a constructed-but-unbound
stub proves nothing.

**Found:** CMX-328 rework round 3, PR #420. The judge's required-mutation-set verdict applied
the round-2 mutation shape to the sibling branch the round-2 fix claimed was already covered,
with the full suite (3471 tests) staying green. Closed by binding the stub in
`test_notifier_never_pulls` and adding `assert stub.sent == []`, and correcting both the
catalog's round-2 addendum prose (above) and
`test_notifier_respects_disabled_notify_on_transition_to_unknown_state`'s docstring, which had
repeated the same unverified claim; verified to fail when the mutation above is re-applied by
hand.

### Round 4 addendum: the round-1 payload fix covered the sentinel branch, and the sibling branch's payload — the thing the round-1 test's own title claims to prove — was never re-checked; plus a brand-new untested pass-through path

**Assertion form, mutations 1-2 — recurrence of [[322|shape 322]] at the one call site round
1 didn't reach:** round 1 added
`test_notifier_fires_when_a_checkout_that_was_unknown_later_falls_genuinely_behind`
specifically to prove that a checkout latched at `UNKNOWN_BEHIND` still gets "the ordinary
'update available' notice" on the sibling ("behind") branch once it crosses the widened edge.
The test proved the notice *fires* (`titles == [...]`) but — exactly shape 322's pattern —
never read the interpolated body or the log's `%d` argument back, on either the push or the
log line. Two other tests of that same sibling branch
(`test_notifier_logs_and_notifies_exactly_once_across_repeated_ticks`,
`test_notifier_never_pulls`) have the identical gap. So the branch's *log line* and *push
body* — not just whether a send/log call happened — were unexercised for three straight
rounds, hiding behind a test whose own docstring already claimed to be about "the ordinary
update-available notice."

**Mutation that defeats it:** blank the interpolated `f"{status.behind} commit(s)
behind..."` argument to `notify.send` (`-> ""`), or blank the log line's `%d` argument
(`status.behind -> 0`), on the sibling branch. Both independently green under the full suite
(3491 tests): the round-1 test's `titles == [...]` assertion reads only the `title=` kwarg,
never `message`; the log assertions across the file grep for the literal prefix
`"update available"`, never the interpolated count.

**Assertion form, mutation 3 — a caller-supplied accumulator's untested pass-through path
is a hardcoded-sentinel mutation waiting to happen:** `check_and_notify`'s `if not
status.ok: return previously_behind` is the transient-failure path — the one that must hand
the caller's own value back unchanged so a blip can never move the sentinel state machine.
Every test of this function (rounds 1-3 included) drives it through a *working* fixture repo,
so `status.ok` is always `True`; nothing in `tests/test_update.py` ever constructs a
`status.ok is False` outcome. An untested branch whose entire job is "return the argument you
were given, unchanged" is indistinguishable, to the suite, from a branch hardcoded to return
any single fixed value — including the sentinel this whole boundary exists to make rare.

**Mutation that defeats it:** replace `return previously_behind` with `return
UNKNOWN_BEHIND` in that branch. Full suite (3491 tests) stays green, because no test ever
reaches the branch at all, let alone with a `previously_behind` other than what
`UNKNOWN_BEHIND` would coincidentally equal.

**Why mutation 3 is a distinct shape from the round-1/2/3 family above, not a fourth
instance of it:** those three are all "a shape that already has a proof somewhere in the
file is reused at a new call site, and the new site's tests inherit the shape's apparent
coverage without re-deriving it." Mutation 3 has no sibling proof anywhere to inherit from —
the branch it targets was never exercised by ANY test, working or not, at any round. It is
the plainer, more familiar gap of an entirely uncovered code path, but it is worth recording
here rather than under a generic "untested branch" heading because of *why* the branch went
uncovered for three rework rounds despite three rounds of focused attention on this exact
function: every fixture in the file is built around a real git checkout with a real
upstream, so producing `status.ok is False` requires either a broken git binary or
monkeypatching `commits_behind` itself — neither of which any existing test needed for its
own purpose, so nobody had a reason to reach for it until a mutation was pointed at the line.

**Guard form that survives:** for mutations 1-2, unpack the actual message and assert the
interpolated count is present (`"N commit(s) behind" in message`), and assert the same on the
matching log record — mirroring the round-1 test's own sentinel-branch payload assertions
(`"no upstream" in message`), applied at last to the sibling. For mutation 3, monkeypatch
`commits_behind` directly to return `UpdateStatus(ok=False, ...)`, call `check_and_notify`
with a `previously_behind` that is neither `0` nor `UNKNOWN_BEHIND` would make it, and assert
the return value equals the input unchanged and no log/notify happened at all.

**Found:** CMX-328 rework round 4, PR #420. The judge's required-mutation-set verdict found
all three mutations above, with the full suite (3491 tests) staying green under each. Closed
by extending
`test_notifier_fires_when_a_checkout_that_was_unknown_later_falls_genuinely_behind` to assert
the sibling branch's interpolated body and log record, and by adding
`test_notifier_blip_does_not_latch_the_unknown_sentinel`; both verified to fail when their
respective mutation is re-applied by hand.
