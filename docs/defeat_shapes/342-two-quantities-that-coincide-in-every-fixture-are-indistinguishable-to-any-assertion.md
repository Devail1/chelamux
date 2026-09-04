## 342. Two quantities that coincide in every fixture are indistinguishable to any assertion, however many assertions you stack

**Assertion form:** the code under test reads one of two named quantities — an argument vs.
its own resolved/derived form, a raw source vs. a fallback, an env var vs. a sibling env var,
a list index vs. a semantically-different index into the same list, a CAS column vs. a
tautology — and every fixture in the file happens to construct a scenario where the two
quantities hold **the same value**. No number of assertions on the result closes the gap,
because the two candidate implementations (the correct one, and the one that reads the wrong
quantity) produce byte-identical output under every such fixture. This is not one shape but a
*family*, recognized after it recurred nine times across nine consecutive judge rounds on one
feature (`dispatcher.acknowledge_blocked_race`, CMX-336) without terminating — each round's
fixture closed the specific pair the previous round's mutation had exploited, and the next
round's mutation simply picked a *different* pair the fixture still hadn't separated:

| round (PR #431) | the two quantities that coincided in every existing fixture |
|---|---|
| 6 | the CAS's `task_id=?` clause vs. a tautology (every fixture inserted exactly ONE row, so `task_id=?` and `(task_id=? OR 1)` match the same rows) · `blocked_race_ack_at` read as merely truthy vs. the real, distinguishable clock value |
| 7 | `judge_sha` vs. `pr_head_sha` (every fixture had them equal, or both NULL) · `--by`'s argparse `default=""` vs. an explicitly-passed `--by` (the CLI test always passed `--by`, so the default itself was never reached) |
| 8 | `ident` (what the operator typed) vs. `run["task_id"]` (what `resolve_run` resolved it to) — every fixture acknowledged by task id itself, where the two strings are identical · the raw `by` argument vs. `who`, the value the fallback chain actually stamped — every fixture passed `by` explicitly, so `"by": by` and `"by": who` read back the same |
| 9 (this entry, closed by CMX-342/#438) | the same `ident`-vs-resolved pair, one hop further downstream — on the **event payload's** `task_id`, not just the function's return value or the DB row · `$USER` vs. `$USERNAME` (every fixture that reached the fallback chain set at most one of the two, so their *relative precedence* — `$USER` must win, per the flag's own `--help` — was never exercised) |

Each round's fix was, correctly, a new fixture that separated the ONE pair its own judge
mutation had exploited — but "the pair a mutation happens to exploit" is not "every pair this
function has", and nothing in the round's own review asked the general question until round 9
made the pattern impossible to miss.

**Mutation that defeats it:** for any of the pairs above, swap the correct quantity for its
coinciding twin at the call site — `who = (by or "").strip() or os.environ.get("USER") or
os.environ.get("USERNAME") or "unknown"` reordered to check `$USERNAME` before `$USER`;
`payload={"task_id": task_id, ...}` swapped to `payload={"task_id": ident, ...}`;
`"WHERE task_id=? AND judge_state=? ..."` widened to `"WHERE (task_id=? OR 1) AND
judge_state=? ..."`. Every fixture built before the pair was identified stays green, because
none of them ever put the two quantities in a state where they disagree. The same shape
recurred, independently, on three *other* branches the same week this was cataloged:

- **CMX-337** (`chela/telegram_status.py` scan): a spinner-frame axis and a row-depth axis
  were each parametrized separately (round 6), so a mutation exempting one frame **at** one
  depth was invisible to both — the fixtures covered "every frame, one depth" and "every
  depth, one frame", never the *product* cell where a specific frame met a specific depth
  (round 7, commit `2f9ff0b`). Same family, on two independently-varied axes instead of two
  named variables.
- **CMX-338** (`chela/telegram_bot.py`, photo relay): a mixed-size fixture always listed the
  fitting image first, so `fits[0]` (the list actually uploaded) and `images[0]` (the first
  image regardless of whether it fit) resolved to the same element — round 8's own commit
  message names it explicitly: *"the same coincide-in-fixtures shape, on a list index"*
  (`75e56b3`).
- **An orchestrator-written wiring guard** on the same feature (CMX-338 round 7) asserted
  `inspect.signature(...)` carried a `files` parameter — pinning the call site's *shape*, not
  what it *did*. The judge defeated it with a three-argument `lambda method, fields, files:
  {"ok": True}` that does nothing: it coincides with the real transport on the one property
  (arity) the guard actually checked, while producing none of the real behavior (round 8,
  commit `75e56b3`, self-diagnosed in the round's own commit message as "SHAPE, not
  behaviour"). Not a two-*variable* coincidence, but the same root cause: the guard proved a
  property both the correct and the wrong implementation share, instead of one that
  separates them.

**Guard form that survives:** ask, of any guard you are about to write — new or already
green — **"which two names does this code read that could hold the same value in the fixture
I'm about to build?"** Candidates to check systematically for any function with more than one
input path to the same conceptual quantity:

- an explicit argument and each link of its own fallback chain (`arg or env_a or env_b or
  default`) — pick a fixture where the argument and the fallback would disagree, and where
  each pair of chain-links would also disagree from each other (env_a ≠ env_b, not just
  env_a set / env_a unset);
- a caller-supplied identifier and whatever it gets resolved/normalized to (`ident` vs.
  `resolve_run(ident)["task_id"]`, a branch/window name vs. a task id) — drive the case where
  the operator names the thing *indirectly*;
- a value threaded through more than one sink (a DB column, a function's return dict, an
  audit-log payload, a rendered string) — read it back from **every** sink independently, not
  just the first one a fixture happens to check (this is [[336|shape 336]]'s own "field never
  independently asserted" half of the same family, now generalized past that one function);
  the fact that a value reached ONE sink correctly says nothing about whether it reached the
  others;
- a CAS/optimistic-concurrency `WHERE` clause with more than one column — construct one
  fixture per column where THAT column is the only one that disagrees between the stale read
  and the real row (this is [[336|shape 336]] round 2/3's "each column needs its own
  fixture" lesson, restated as the general question);
- a list index chosen for a semantic reason (`fits[0]`, "the first eligible item") vs. a
  positional index (`images[0]`, "the first item, period") — order the fixture so the
  semantically-correct item is **not** first;
- two axes varied independently across separate parametrized fixtures — add at least one
  fixture that varies **both together**, so the cell where they intersect is not only ever
  reached with one of them held at its default.

None of these require a *new kind* of test infrastructure — every fix across all nine CMX-336
rounds, and CMX-337/338's recurrences, was "the same fixture shape, with one more value made
to differ." The failure mode is entirely in not asking the question before the judge asks it
for you, one round at a time.

**Why this needed its own entry, not just a pointer to [[336|shape 336]]:** shape 336's entry
documents five rounds of this exact pattern but frames each fix as closing a *specific named
field* on one function (`by`, `at`, `note`, `sha`, then `task_id`/`pr_url`) — reading it
teaches "enumerate this payload's fields", not "ask this question about any two quantities
anywhere". The recurrence on CMX-337 (two independently-parametrized *axes*, not payload
fields) and CMX-338 (a *list index* and a *signature-shape* guard, not a fallback chain) is
what makes the general question — not the field-enumeration checklist — the transferable
lesson.

**Found:** CMX-336 rounds 6-9 (PR #431, commits `8e76dd6`, `19448e4`, `2c5c902`, and round 9 —
the last two pairs — recorded only in commit messages and test docstrings until this entry).
Round 9's two remaining pairs were closed on CMX-342 (issue #438) by
`test_acknowledge_event_payload_records_the_STAMPED_task_id_when_acknowledged_by_branch_name`
and `test_acknowledge_by_prefers_env_USER_over_USERNAME_when_both_are_set`
(`tests/test_dispatcher_blocked_race_ack.py`) — the first drives an acknowledgement by branch
name and reads the event payload's `task_id` back independently of the function's return
value (closing the gap between [[336|shape 336]] round 5's "read every sink independently"
and round 8's "resolve before you stamp", one sink further downstream than round 8 reached);
the second sets `$USER` and `$USERNAME` to two distinct values and asserts which one the
fallback chain actually picked, rather than only ever setting one of the two.

**See also:** [[336|shape 336]] — five of this family's nine rounds, framed per-field on one
function; this entry generalizes past that framing. [[306|shape 306]] — a related but
narrower shape: a fallback expression's operands collapse onto the same identity because a
fixture has too few *items* (a one-item list) for the operands to diverge; this shape is the
general case, where the coinciding pair need not be list elements at all, and the fix is
"make the two named quantities disagree", not specifically "add a second item".
