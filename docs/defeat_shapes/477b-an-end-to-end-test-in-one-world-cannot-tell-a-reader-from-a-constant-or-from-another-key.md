## 477b. An end-to-end test in ONE world cannot tell a reader from a constant, or from another key

**Assertion form:** the fix for [[477|shape 477]]: one healthy-path test drives the registered
fact from real files and asserts every printed line. Every key of the gathered state is
seeded, so every key looks pinned.

**Mutation that defeats it:** in the healthy world several answers coincide, so one can stand
in for another, or for a literal:

```diff
- "packaged_version": packaged_version(),
+ "packaged_version": (installed_state() or {}).get("version"),
- "statusline": statusline_wired(),
+ "statusline": True,
- "sessions": session_sources(cache_dir)}
+ "sessions": session_sources(cache_dir, stale_s=1e12)}
```

A fresh install's version IS the package's, the status line IS wired, and no file is stale.
The warnings these keys exist for (an outdated copy, another status line, a dead feed) can no
longer fire, and their unit tests still pass because those build the state by hand.

**Guard form that survives:** a second end-to-end world in which every answer differs from the
first AND no two keys agree (old copy, other recorded dir, someone else's command, one stale
file, one session with both files), plus the empty world. Assert the whole list of
`(level, title, detail)`, so a warning that stops firing is a missing row.

**Found:** CMX-477 rework round 2, PR #1000, by hand after a held-out experiment survived (its
text was not disclosed). Closed by
`test_the_doctor_fact_end_to_end_when_every_answer_differs` and
`test_the_doctor_fact_end_to_end_with_nothing_there` in `tests/test_measure_mod.py`.
