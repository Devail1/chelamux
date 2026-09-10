## 358. A new event kind ships behavioral tests but skips the per-kind wiring assertion every sibling kind already has, at every one of its independent consumer sites

**Assertion form:** `run_unjudged_merged` is a new member of a family of judge-verdict event
kinds (`run_judge_clean`, `run_judge_cannot_verify`, `run_judge_blocked_race`, …) that fans
out across four independent files: the emitter (`chela/inbox.py`, which picks the `kind`
string and populates its `payload`), and three consumers that each key on that exact literal
— the dashboard's Decisions panel subscription list (`decisions.js`'s `DECISION_TYPES`), the
feed's lane classifier (`feedmodel.js`'s `TYPE_CLASS`), and the event log itself
(`chela events --type`). Every existing sibling kind already has its own dedicated assertion
at each of those four sites — `decisions.test.mjs` pins each kind's `DECISION_TYPES`
membership one at a time, `feed.test.mjs` pins each kind's `classOf()` mapping one at a time,
and `test_inbox.py` pins each kind's literal `queued[0]["kind"]` string and its
`payload["judge_state"]`/`payload["judge_detail"]` fields one at a time. The PR that added
`run_unjudged_merged` wrote real, passing tests — `test_an_unjudged_merge_fires_its_own_event`,
`test_an_unjudged_merged_run_fires_exactly_once`,
`test_the_unjudged_merged_reason_is_EXCERPTED_into_the_summary` — but every one of them reads
only the rendered **summary text** (`"NO judge verdict" in text`, `"pull/9" in text`,
`_LONG_DETAIL[:40] in summary`). None of them opened `decisions.js`, `feedmodel.js`, or
asserted the emitted `kind` string / payload fields directly, even though the sibling kinds
sitting one function away in the same three files all do exactly that.

**Mutation that defeats it:** four independent one-line swaps, one per site, each invisible
to the suite (`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`: 3921 passed, 0 failed both before
and after each):

```diff
# chela/dashboard/static/js/decisions.js — unsubscribes the Decisions panel; the scoped
# /api/log fetch never asks for this kind again
-     'run_unjudged_merged',
+     'run_unjudged_merged_NOT_SUBSCRIBED',
```

```diff
# chela/dashboard/static/js/feedmodel.js — the kind falls through classOf()'s default and
# renders as an anonymous `·`, indistinguishable from unrelated noise
-     run_unjudged_merged: 'run',
+     run_unjudged_merged: 'other',
```

```diff
# chela/inbox.py — the literal every consumer keys on is renamed; the summary-text tests
# above still pass because none of them read `kind`
-                 "run_unjudged_merged",
+                 "run_unjudged_merged_x",
```

```diff
# chela/inbox.py — the verdict never reaches the durable payload, only the one-shot push
-             payload["judge_state"] = judge_state
-             payload["judge_detail"] = run.get("judge_detail")
+             payload["judge_state"] = ""
+             payload["judge_detail"] = None
```

Each of the three behavioral tests keeps passing under all four mutations: the summary line
they read is built from `run.get("judge_detail")` and the f-string template directly, not
from `payload["judge_state"]`/`payload["judge_detail"]` or the `kind` argument to `_event()`,
so blanking the payload fields or renaming the kind string changes nothing they observe.

**Why this slips through even though the feature has tests:** the tests were written to prove
"the feature works" — a merge with no verdict fires exactly one push, and the push's wording
is right — which is a true and useful claim, but a different claim from "every downstream
consumer that keys on this kind's exact spelling still receives it." A reviewer skimming the
diff sees three new passing tests next to three new production lines and reads that as
coverage, the same way updating one of two sibling renderers' tests read as "the feature has
a test" in shape [310](310-a-sibling-renderer-s-changed-clause-has-no-assertion-of-its-own.md)
— except here it is not one sibling site missed, it is the entire category of wiring
assertion (kind literal + payload fields + JS-side subscription + JS-side classification)
that has no test at all, at every site, because all three tests share the same blind spot
(reading the summary, never the structured fields).

**Guard form that survives:** when a new member joins a family whose existing members already
have a **per-member** assertion pattern at N independent sites (a subscription list, a
classification table, a payload-carrying literal), the new member needs the *same* assertion
at *every one* of those N sites — not a behavioral test that merely proves the feature fires.
Enumerate the sites by finding where the existing siblings are each individually asserted
(`grep` the sibling kind's exact string across `tests/`), not by trusting that a green suite
with new tests in it means the new member got the same treatment. Before trusting the fix,
hand-apply each site's exact mutation (rename the subscribed literal, flip the classification
target, rename the emitted kind, blank the payload field) and confirm each one independently
turns a *specific* new test red — a behavioral test passing under all four is not evidence
any of them is covered.

**Found:** CMX-358 rework round 2 (2026-09-10), PR #483. The judge applied all four mutations
above to a throwaway checkout of the PR's head; `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
(green before every mutation — 3921 passed, 0 failed) stayed green under each one. Closed by
adding `🔴 GUARD: the decisions panel subscribes to the unjudged-merged kind` to
`tests/decisions.test.mjs`, a `classOf('run_unjudged_merged') === 'run'` assertion to
`tests/feed.test.mjs`, and
`test_an_unjudged_merged_events_kind_and_payload_are_the_literals_every_consumer_keys_on` to
`tests/test_inbox.py`, asserting `queued[0]["kind"] == "run_unjudged_merged"` and
`payload["judge_state"]`/`payload["judge_detail"]` against a real `inbox.tick()` call — the
same shape as each sibling kind's existing assertion at each site, verified red against all
four mutations individually before landing.
