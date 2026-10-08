## 28b. A resolver swapped in at every consumer is tested only at the consumers the bug report named

**Assertion form:** a fix replaces one lookup with a smarter resolver at *every* consumer of
a shared feed — here `status_map["by_pid"].get(cpid)` became
`agent_manager.session_entry(cpid, status_map)` (which follows a session that moved to a
Claude Code background session) in six places: `status_by_wid`, `/api/agents`, `peek`,
`chela status`, `collab._agent_rooms` and `notify.waiting_windows`. The bug report named
the Wall, the sidebar and peek, so the tests drove exactly those, and they asserted the
fields the report named (`status`, `session_name`) on each.

**Mutation that defeats it:** revert any consumer the report did NOT name to the old
lookup (`collab._agent_rooms` back to `status_map["by_pid"].get(cpid)`), or add a
condition that excludes the new case there (`notify`: `if entry and not entry["moved"] and
…`). Also: revert a field the report did not name inside a consumer it DID name
(`/api/agents` reading `cwd` from `cwd_by_pid[cpid]` again; `peek` dropping `session_id`).
Green, because the test fixture parks `cwd_by_pid` on `{}` — the old lookup and the new one
both read `None` — and no test reads `session_id`/`session_kind` or the "moved" wording
back. Suite green under each.

**Guard form that survives:** enumerate the consumers from the diff, not from the bug
report — every call site the resolver was swapped into gets one test that drives the new
case (a moved session) through that consumer and asserts its output. For each field the
resolver returns, give the fixture a value only the followed entry carries (a `cwd` on the
descendant pid alone), so the old key and the new one can no longer read the same default.
Pin rendered text in full (`== ["  session: … — moved to a background session; address it
by this name"]`), with a control case asserting the note is absent when nothing moved, so
both `if True` and `if False` go red.
