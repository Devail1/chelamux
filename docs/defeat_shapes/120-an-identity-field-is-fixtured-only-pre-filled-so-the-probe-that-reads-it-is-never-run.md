## 120. An identity field is fixtured only pre-filled, so the probe that reads it is never run — and the consumer's fallback match hides its absence

**Assertion form:** a latch is re-keyed from an unstable value (a float start that jitters
between ticks) onto a stable identity (the process's pid). The consumer is tested thoroughly:
same pid over N ticks → one notice, a new pid → news again, no pid → fall back to the start
with slack. Every one of those tests hands the consumer a probe dict / tuple **built by the
test, with the pid already in it.** The producer — `_selfcheck_scan`, which reads the pid off
the `/proc/<pid>` directory name — is never run by any test.

**Why that's a gap:** the tests prove the consumer *would* key on the pid if it got one; none
proves the probe *gives* it one. And the consumer has a deliberate fallback for a missing pid
(match the start within 120s), which on every "same self-check, jittering start" fixture
produces exactly the right answer. So a producer that emits `None` for the pid degrades the
latch to the old float-with-slack behaviour, and the only case that tells them apart — a
quick re-run, a NEW process whose start lands inside the slack — is fixtured only on the
consumer side, with hand-built pids.

**Mutation that defeats it:** in `_selfcheck_scan`, `int(d.name)` → `None`. Suite green
(7643 passed) — while in production a self-check re-run within two minutes of the first is
taken as the same self-check and never announced.

**Guard form that survives:** run the PRODUCER over a fake source (a monkeypatched `_PROC`
pointing at a tmp tree with `stat`/`cmdline`/`cwd`/`stat`) and assert the identity it reports
is the one the source carries (distinct per process, never None for a readable one). Then
drive the whole chain once — producer → probe → latch — on the one input where identity and
the fallback disagree (a new pid inside the fallback's slack must fire). When a consumer has
a fallback for a missing field, a consumer-only test of that field is satisfied by the
fallback; the field's producer needs its own test. Related: [[75b|shape 75b]],
[[86b|shape 86b]], [[16|shape 16]].

**Found:** CMX-120 rework round 1 (2026-10-13), PR #675 — the judge's wiring experiment on
`_selfcheck_scan`'s pid survived the full suite.
