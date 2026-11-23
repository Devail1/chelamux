## 477. A report is fed a hand-built state, and its gatherer is run only where an empty read is a valid answer

**Assertion form:** a doctor fact is split in two: `read_state()` gathers (installed files,
settings, the cache dir) and `report(declared, state)` prints. The report tests build `state`
by hand. The leaf readers (`newest_limits(dir)`, `session_sources(dir)`) are tested with the
directory passed in. The gatherer itself DOES run, through the fact registry's corruption
test, but that test corrupts one key of the state (the installed mod's recorded dir) and
asserts only the level that key raises.

**Mutation that defeats it:** point the gatherer at a directory nothing writes:

```diff
- cache_dir = config.CHELA_DIR / "context"
+ cache_dir = config.CHELA_DIR / "contexts"
```

Nothing raises: a glob over a missing directory is empty, and empty is this reader's honest
"no producer" answer ("limits: unknown", "no session has a fresh snapshot", both OK). In
production doctor says unknown forever beside a working feed.

**Why this is distinct from [[361|shape 361]]:** there the default was never executed. Here
the mutated line runs in the suite; every run of it is in a world with no files, where the
right and the wrong directory read the same.

**Guard form that survives:** one healthy-path test through the registered fact
(`fact.report(fact.declare(), fact.read_back())`), with real files seeded for EVERY key of the
state, at paths spelled out independently of the code, and the printed lines asserted whole.
A state key left at its empty value in that test is a key still unpinned.

**Found:** CMX-477 rework round 1, PR #1000. Closed by
`test_the_doctor_fact_reads_this_chelas_files_end_to_end` in `tests/test_measure_mod.py`.
