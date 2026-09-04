## 342c. A newly-read sink inherits the old coincide-in-fixtures gap, because "we now read it back" is not the same claim as "we read it back with values that could disagree"

**Assertion form:** [[342b|shape 342b]] fixed the gap where the `summary` string — a THIRD
sink built from the same local variables (`task_id`, `who`, `clean_note`) as an already-tested
`payload` dict — was never read back at all, by adding
`test_acknowledge_event_summary_renders_the_task_id_who_and_note` and
`test_acknowledge_event_summary_omits_the_dash_when_there_is_no_note`. Both fixtures pass the
task id itself as the identifier (`ident == task_id == "abc123"`) and pass `by` explicitly
(`by == who == "someone-distinctive"`) — exactly the two pairs [[342|shape 342]] round 8
already named as needing a fixture where they DISAGREE, on the DB row and the payload dict.
Fixing "this sink is never read" does not automatically fix "this sink is read with fixtures
that can't tell two different f-string arguments apart" — the newly-added assertions inherit
whatever coincidences the fixture already had, because reading a sink and separating the
values rendered into it are two independent properties of a test.

**Mutation that defeats it:** swap either f-string argument for its coinciding twin —
`f"{task_id}: blocked-race verdict acknowledged by {who}"` becomes `f"{ident}: ..."` (naming
the identifier the operator typed instead of the resolved run) or `f"...by {by}"` (naming the
raw, possibly-empty argument instead of the actor the fallback chain actually stamped). Both
new 342b tests stay green under either mutation: `"abc123" in summary` still holds because
`ident` and `task_id` are the same string in that fixture, and `"someone-distinctive" in
summary` still holds because `by` and `who` are the same string in that fixture.

**Guard form that survives:** when a fix closes a "this sink is never read" finding by adding
assertions on that sink, immediately re-run [[342|shape 342]]'s own question against the NEW
fixture, not just the sink: "which two names does *this* f-string read that could hold the
same value in the fixture I just wrote?" Here that means acknowledging by BRANCH name (so
`ident` = `"cmx-336"` disagrees with the resolved `task_id` = `"abc123"`) and omitting `--by`
entirely (so `by` = `""` disagrees with the env-chain-filled `who`), then asserting the summary
contains the resolved/stamped value and — where the mutation's replacement text is itself a
plausible-looking string, not merely absent — that it does NOT contain the wrong one.

**Why this needed its own entry, not folding into [[342b|shape 342b]]:** 342b's entry is about
*discovering a sink exists and is unread*; this entry is about a different, later failure that
occurs even after that discovery — the fixture used to close the first gap can still be too
weak to close the second. Filing it under 342b would conflate "found the sink" with "the sink
now separates every value it renders," which are the two halves of covering a sink completely
and failed at different rounds for different reasons.

**Found:** CMX-342 (PR #445), judge round 2, immediately after round 1 closed
[[342b|shape 342b]]. `chela/dispatcher.py`'s `acknowledge_blocked_race` summary f-string;
the judge swapped `task_id`→`ident` and `who`→`by` in two separate throwaway checkouts of the
same PR head, and `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3697 passed) stayed green under
both. Closed by
`test_acknowledge_event_summary_names_the_RESOLVED_task_id_not_the_typed_identifier` and
`test_acknowledge_event_summary_names_the_STAMPED_actor_not_the_raw_by_argument`
(`tests/test_dispatcher_blocked_race_ack.py`), which acknowledge by branch name and by env
fallback respectively, and assert the summary contains the correct value — the first also
asserts the raw identifier is absent, since a rendered f-string's wrong value is a plausible
string, not an empty one.

**See also:** [[342|shape 342]] — the general "two quantities that coincide in every fixture"
family this is a direct instance of, one round later, on the same function. [[342b|shape
342b]] — the "sink never read at all" gap this entry's own fixtures were built to close, before
turning out to still coincide.
