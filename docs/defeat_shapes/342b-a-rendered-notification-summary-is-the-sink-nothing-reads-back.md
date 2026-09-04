## 342b. A rendered notification summary is the sink nothing reads back, because the payload dict sitting right next to it looks like proof enough

**Assertion form:** a function emits one audit event carrying the same fact through two
sinks built at the same call site — a human-readable `summary` string (an f-string) and a
structured `payload` dict — and the test suite enumerates the payload's fields one by one
(`payload["by"]`, `payload["sha"]`, `payload["note"]`, `payload["at"]`, `payload["task_id"]`),
closing exactly the gap [[336|shape 336]] and [[342|shape 342]] describe: "a value threaded
through more than one sink must be read back from EVERY sink independently." Each of those
fixes reads that lesson as "enumerate the payload's fields" and stops there, because the
payload is the sink every test in the file already touches. `event_log.append`'s own
docstring says plainly that `summary` "is what a notification renders" — a THIRD sink,
built from the exact same local variables (`task_id`, `who`, `clean_note`) as the payload,
sitting one argument to the left of it in the same call — but nothing in the file ever reads
`events[-1]["summary"]` back, because the payload dict already "looks like" the proof that
the event carries the right information.

**Mutation that defeats it:** replace the summary argument with a bare empty string —
`f"{task_id}: blocked-race verdict acknowledged by {who}" + (f" — {clean_note}" if
clean_note else "")` becomes `""` — while leaving the `payload={...}` dict on the very next
lines untouched. Every payload-field assertion, every DB-column assertion, and the bare
"an event of this type fired" assertion all stay green: none of them names the `summary` key
at all. The operator-facing notification text — the one thing a human actually reads when
this event fires — is silently empty, and the PR that catalogs "read every sink
independently" as its own headline lesson ships with exactly one of that event's three sinks
unread.

**Guard form that survives:** when a function builds more than one artifact from the same
local variables at one call site — a DB row, a return dict, an audit-log payload, AND a
rendered/human-readable string — enumerate the SINKS themselves before writing the fixture,
not just the fields inside whichever sink happens to be a dict already. A payload dict is
easy to enumerate because `payload["x"]` reads like an obvious assertion to write; a
free-form rendered string invites skipping past it precisely because there is no field name
to check off a list — so treat "is there a rendered/summary string next to this payload" as
its own checklist item, separate from "did I read every field of the payload."

**Why this needed its own entry, not folding into [[342|shape 342]]:** shape 342 generalizes
the family to "read every sink independently" and lists rendered strings as one example
sink in its own guard-form section — but its own worked fixes (round 9, closed on this same
task) only ever demonstrated the payload-dict half of that list, on the very function this
entry is about. The gap survived the entry that named it, on the same function, in the same
PR that added the entry — which is itself worth recording: naming a sink in a catalog's prose
does not guarantee the next fixture on the same function actually reads it.

**Found:** CMX-342 (PR #445), judge round 1. `chela/dispatcher.py`'s
`acknowledge_blocked_race` builds `event_log.append("blocked_race_ack", <summary
f-string>, payload={...})`; every one of round 9's own new tests
(`test_acknowledge_event_payload_records_the_STAMPED_task_id_when_acknowledged_by_branch_name`,
`test_acknowledge_by_prefers_env_USER_over_USERNAME_when_both_are_set`) and every pre-existing
payload test in `tests/test_dispatcher_blocked_race_ack.py` read `events[-1]["payload"][...]`
but none read `events[-1]["summary"]`. The judge emptied the summary string in a throwaway
checkout; `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3680 passed). Closed by
`test_acknowledge_event_summary_renders_the_task_id_who_and_note` and
`test_acknowledge_event_summary_omits_the_dash_when_there_is_no_note`, which read the summary
string back and assert it names the task, the actor, and (conditionally) the note.

**See also:** [[336|shape 336]] and [[342|shape 342]] — the "read every sink independently"
family this entry is one further instance of, on a sink neither of them's own fixes actually
exercised.
