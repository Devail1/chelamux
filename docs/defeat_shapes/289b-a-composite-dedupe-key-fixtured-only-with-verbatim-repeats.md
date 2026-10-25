## 289b. A composite dedupe key fixtured only with verbatim repeats

**Assertion form:** a merge of two lists drops an item already present, by a composite key —
`key(e) = (e["file"], e["before"], e["after"])`. The test feeds one item that appears in both
lists, copied verbatim, and asserts it appears once.

**Mutation that defeats it:** drop any component of the key — `(e["file"], e["before"])` or
`(e["before"], e["after"])`. A verbatim copy still matches on every remaining component, so it
is still dropped and the test stays green. Live, two different experiments that share a
`before`/`after` in different files (or the same line mutated two ways) collapse into one, and
one of them is silently never run.

**Why the existing test doesn't catch it:** a verbatim repeat agrees on EVERY component, so
it proves only that the key is *some* subset of the fields. Nothing is ever fed that differs
on one component and agrees on the rest — the only input that tells the full key from a
truncated one.

**Guard form that survives:** for each component of the key, one near-miss item that differs
on that component alone; assert it is KEPT. Together with the verbatim repeat (DROPPED), that
pins the key exactly.

**Found:** `chela.verdictcarry.with_prior` (CMX-289, PR #878, round 4 — found by the agent's
own sweep while chasing a held-out survivor). The same sweep found two more guards that only
fed the positive case: `prompt_note` with an empty recorded sha, and the live `_judge_vars`
wiring never called with the recorded head itself (passing `""` for the head stayed green).

**See also:** [[86b|shape 86b]] — a once-per-key latch with a single key; [[66|shape 66]] — a
two-axis predicate whose negative control covers only one axis.
