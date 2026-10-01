## 424. A shared identity builder feeds both the fixtures and the writer, so the writer dropping it is invisible

**Assertion form:** a fix adds a field to a record (here `start_ticks`, the boot-clock
identity on a judge lock and run status) and introduces ONE builder (`owner_identity()`) that
the production writers are supposed to use. The tests build their records with that same
builder, then exercise the reader under the hostile condition (a stepped wall clock) and
assert it still answers correctly. The assertion is real and goes red if the READER breaks —
but every record it ever sees came from the builder, never from the writer.

**Mutation that defeats it:** make a production writer stop using the builder — write
`{"pid": …, "started": …}` by hand, dropping the new field. The reader falls back to the old
path for that writer's records only, the fixtures still carry the field, and the suite stays
green. Here both writers (`_claim_judge_slot` and `judge_run`'s status) could each drop
`start_ticks` and nothing noticed.

**Guard form that survives:** for each production writer, drive the WRITER itself (claim a
real slot; start a real `judge_run` and look mid-battery), read back what it put on disk, and
apply the hostile condition to THAT record. A fixture built by the helper the writer was meant
to call proves the helper and the reader agree — not that the writer calls the helper. Count
the writers (`git grep` the record's file/key) and give each one its own wiring test.

**Found:** CMX-424 round 1, 2026-10-01 — both writer mutations survived a 4877-test suite.
