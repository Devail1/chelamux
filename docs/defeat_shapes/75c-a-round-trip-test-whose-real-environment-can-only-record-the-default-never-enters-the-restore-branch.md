## 75c. A round-trip test whose real environment can only record the DEFAULT never enters the restore branch — and the fixture that stubs a sweep out hides its only caller

**Assertion form:** a capture → restore round trip (archive a session, then resume it) is
proven end-to-end against a REAL environment — a private tmux server, a real store, a stub
`claude` — and asserts the fields it can see come back: same cwd, same window name,
`--resume <id>` on the command line. That test is honest and expensive, which is exactly why
nobody notices what it *cannot* exercise: the optional fields the record also carries.
In a scratch tmux server there is no Remote Control session (`rc_name` records `None`) and no
human renamed the window (`manual_name` records `False`), so every restore branch keyed on
those fields — `remote_control_name=record.get("rc_name") or None`,
`if record.get("manual_name"): mark_manual_name(wid)` — is skipped by construction. The
round trip passes whether those branches exist or not.

The sibling: a sweep (`drop_resumed_elsewhere` — forget an archived session that is running
again) whose only production caller is a route, and the route fixture stubs the sweep to
`lambda is_live: []` so the other route tests don't need a session probe. No test drives the
real sweep, so `if False and ... is_live(...)` keeps every resumed session archived forever
and nothing goes red.

This is shape [[02]] (a fixture parked on a default) seen from the other end: the fixture
was not *chosen* to hold the default — the realistic environment can only *produce* it.

**Mutation that defeats it:** `remote_control_name=record.get("rc_name") or None)` →
`remote_control_name=None)`; `if record.get("manual_name"):` →
`if False and record.get("manual_name"):`; the sweep's comprehension prefixed with
`if False and`.

**Guard form that survives:**
- For each optional field the record carries, one in-process test that seeds a record with
  the NON-default value and spies the restore call, asserting the value reaches it
  (`remote_control_name == "My Proj"`, `mark_manual_name` called with the new wid) — plus the
  default-valued twin, so the branch is pinned both ways.
- Pin the CAPTURE side too: a pane with `manual_name=True` and a stubbed RC name must land in
  the written record, so dropping a field at archive time is caught as well as at resume.
- A function that a shared fixture stubs out needs its own direct test (two records, one live,
  one not — only the live one leaves) AND one route test that puts the real function back
  (captured at import, before any fixture patches it).

**Found:** CMX-75 rework round 2 (2026-10-11), judge battery on PR #640.
`chela/sidebar_archive.py`'s `unarchive_session` / `drop_resumed_elsewhere`;
`tests/test_sidebar_archive.py`'s live round trip recorded `rc_name=None` and
`manual_name=False`, and its `client` fixture stubbed `drop_resumed_elsewhere`, so all three
mutations left the full suite green (6563 passed). Closed by the `resume` fixture's two
restore tests, `test_archive_records_the_panes_manual_name_flag_and_remote_control_name`,
`test_drop_resumed_elsewhere_forgets_only_the_sessions_running_again`, and
`test_the_archive_route_sweeps_out_a_session_resumed_by_hand`.
