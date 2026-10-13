## 100. A write-then-read round-trip test seeds the record by hand, so the producer's own write is unpinned

**Assertion form:** a producer persists something at startup (`main()` calls
`record_session(dir, sid, argv)`) and a consumer later reads it back (`fake --resume <sid>`
re-runs `argv`). The test calls the write helper directly with a hand-built `argv`, then drives
the consumer and asserts it re-runs exactly that `argv`. It reads as "the round trip is
covered".

**Why that's a gap:** the test is its own producer. Nothing reaches the line in `main()` that
calls the writer, nor the argument list `main()` builds for it — so whether a real session
records itself at all, and whether what it records carries the fields the consumer needs (here
`--session-id`, so a resumed session keeps the id the UI knows it by), is invisible. The
helper works, the reader works, and the wiring between production and the helper is dead to
the suite. Close cousin of [[338|shape 338]] (a kwarg proven only by direct construction) and
[[394|shape 394]] (a validator fixtured only by the honest writer) — the difference here is
that the *consumer* half is driven for real, which makes the hand-seeded producer half easy to
mistake for coverage.

**Mutation that defeats it:** `if False: record_session(...)` at the producer's call site (or
drop `--session-id` from the argv it builds). The helper test seeds its own record; the
consumer still finds it; the suite stays green — while in production Unarchive has nothing to
resume.

**Guard form that survives:** drive the producer's real entry point (`main()` with the
long-running body stubbed out), read the record it wrote off disk, assert its exact contents,
and then feed *that* record — not a hand-built one — to the consumer. Cover the defaulted
branch too (no id passed ⇒ the record is keyed by, and carries, the id the session actually
runs as).

**Found:** CMX-100 rework round 1 (2026-10-13), PR #671 — `scripts/demo/claude_demo_agent.main`'s
`record_session` call survived. Closed in `tests/test_demo_resume.py` by
`test_a_started_session_records_itself_so_resume_reruns_it` and
`test_a_session_started_without_an_id_records_under_the_id_it_runs_as`.
