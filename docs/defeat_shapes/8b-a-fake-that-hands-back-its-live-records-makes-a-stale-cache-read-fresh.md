## 8b. A fake that hands back its live records makes a stale cache read fresh

**Assertion form:** a read must bypass a per-tick cache (`cache=False`) so that it sees a write
made earlier in the same tick — the Linear `archive_sweep` that `close_tasks` runs right after
marking an issue Done (CMX-8). The test stubs the transport with an in-memory fake whose
responses are built from its OWN record dicts, not copies of them. A write mutates those dicts
in place, so a response cached before the write changes along with it.

**Mutation that defeats it:** flip the read to `cache=True`. The cached response is the same
dict objects the fake has since mutated, so the "stale" read already shows the closed issue,
and the sweep archives the right one. The suite stays green. Live, a real transport returns
fresh JSON, and the sweep replays the first read: the issue it just closed still looks open,
and the team creeps over `keep_done` until the next sweep.

**Guard form that survives:** make the fake behave like the wire. Return a deep copy per
response, so nothing a test reads back aliases the fake's state. Then fill the cache on purpose:
run the same read once on the SAME source instance (here, a sweep with nothing to archive), make
the write, and assert the second read's outcome depends on the write (the oldest issue is
archived, and the transport was called twice). A test that starts with an empty cache passes
whether or not the bypass is there.

**Related:** [[421b|entry 421b]] (a cache reset never read inside the cache window). Both need
the cache full when the guarded read runs. This entry is about the fake quietly keeping the
cache fresh, even when it is full.
