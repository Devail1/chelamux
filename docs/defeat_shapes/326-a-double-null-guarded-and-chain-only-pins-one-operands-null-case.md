## 326. A double-null-guarded `and`-chain only pins one operand's null case

**Assertion form:** a predicate is written as `bool(a and b and a != b)` specifically so
that EITHER operand being unset is conservative — treated as "not proven," not as a positive
match. The suite carries a negative control for that conservatism, but only along one
operand's axis: a fixture with `a` set and `b` unset, asserting the predicate stays `False`.
Nothing seeds the mirror fixture (`a` unset, `b` set), because the one helper every other
test in the file routes through defaults BOTH fields to the same falsy value, so no other row
in the suite can even construct the missing combination by accident.

**Mutation that defeats it:** drop the first operand's guard from the chain —
`bool(judge_sha and head_sha and judge_sha != head_sha)` → `bool(head_sha and judge_sha !=
head_sha)`. The tested axis (`b` unset) is untouched: `head_sha` falsy still short-circuits
the `and` before the comparison runs, so that fixture still returns `False`. The untested axis
now breaks silently: with `judge_sha=None` and `head_sha` set, `None != head_sha` is `True`,
so the mutated predicate now reports staleness for a row the original code deliberately left
unproven — and no fixture in the suite ever constructed `judge_sha=None, pr_head_sha=<set>`
to notice.

**Why the existing negative control doesn't catch it:** `bool(a and b and a != b)` con­serves
on TWO independent axes (`a` missing, `b` missing), not one. A test that only ever unsets `b`
proves `b`'s guard is load-bearing; it says nothing about whether `a`'s guard is *also*
load-bearing or was actually redundant with the comparison and safe to drop. The two null
checks look like one "leave it conservative" idea in the docstring, but each is a distinct
clause the mutator can remove independently, and a single one-sided fixture can only ever kill
one of the two removals.

**Guard form that survives:** for any predicate that null-guards **N** operands before
comparing them, write **N** separate negative-control fixtures — one per operand, each
leaving exactly that operand unset while the others are populated — not one fixture that
happens to unset whichever operand was top of mind. A docstring that states the conservatism
"either way" is a direct cue that two fixtures, not one, are required.

**Found:** `chela.automerge._judge_verdict_is_stale` (CMX-326, PR #415, round 1).
`tests/test_automerge.py::test_candidates_includes_a_clean_verdict_with_no_head_sha_recorded_yet`
pinned `pr_head_sha=None` with `judge_sha` set; nothing pinned the mirror
(`judge_sha=None`, `pr_head_sha` set) because `_seed_run`'s defaults make every other row in
the file leave `pr_head_sha` falsy too, so it could never exercise that branch of the `and`
either. `chela judge` mutated `bool(judge_sha and head_sha and judge_sha != head_sha)` to
`bool(head_sha and judge_sha != head_sha)` — `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
stayed green (3420 passed) with the mutation applied. Closed by adding
`test_candidates_includes_a_clean_verdict_with_no_judge_sha_stamped`, which seeds
`judge_sha=None, pr_head_sha="deadbeef0001"` and asserts the row is still a candidate.

**See also:** [[66|shape 66]] — the same "one axis of a conjunction is tested, the other is
not" gap, on a regex-widening mutation instead of a null-guard removal; [[49|shape 49]] — a
two-sided property where both *negative* branches were guarded but the one *positive* branch
was not, a related but distinct asymmetry.
