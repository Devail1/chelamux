## 24. A subprocess fake dispatches on an argv prefix, so a wrong flag beyond the prefix still gets fabricated correct-shaped data back

**Assertion form:** a test fakes `subprocess.run` to answer a real external command
(`systemctl --user show <unit> -p ...`). The fake routes on a short prefix of `cmd`
(`cmd[:3] == ["systemctl", "--user", "show"]`) plus one positional argument used as a
lookup key (`unit = cmd[3]`), then fabricates stdout from a per-unit fixture dict keyed
only on that unit name. Every property flag after the key (`-p MemoryMax -p
MemoryCurrent`, `--type=slice`) is never inspected — the fake cannot tell which
properties or unit-type filter production actually asked for, only that *some* call
matching the prefix happened.

**Mutation that defeats it:** change what property or filter the real call asks for,
without changing the prefix or the unit — `-p MemoryMax` → `-p MemoryHigh` (a different,
real systemd property), or `--type=slice` → `--type=service` in the discovery call. The
fake still matches on `cmd[:3]`/`cmd[3]`, still hands back the fixture's `MemoryMax=...`
line or `.slice`-named units regardless, so production's parser reads exactly the value
the fixture author intended — even though systemd would never return that property (or
those units) for the request production actually issued. Every assertion downstream
passes; on a real box the query returns nothing and the feature silently reports "off"
forever.

**Why this is distinct from shape 18:** shape 18 is a stub whose return value is a fixed
literal, blind to *any* argument (`lambda fmt: "12:34:56"`). Here the fake is NOT
argument-blind in general — it correctly varies its answer per unit name, which makes it
look far more rigorous than a canned constant. The gap is narrower and easier to miss:
the fake discards only the tail of the argv (the actual query semantics — which property,
which unit type), while still convincingly discriminating on the part it does read.
"This fake varies its output, so it must be checking what was asked" is the trap.

**Guard form that survives:** assert the full trailing argv the fake dispatches on, not
just the routing prefix, and not just membership of one flag within it — `assert cmd[4:]
== ["-p", "MemoryMax", "-p", "MemoryCurrent"]` for the `show` call, `assert cmd[3:] ==
["--type=slice", "--state=active", "--no-legend", "--plain", "--no-pager"]` for the
`list-units` call — so a request for the wrong property, unit type, *or any other flag in
the same command* raises inside the fake itself instead of silently returning fixture
data shaped as if the request were correct. (An earlier version of this entry recommended
`assert "--type=slice" in cmd` for the `list-units` call; that membership check is itself
an instance of shape 25 below and was defeated the round after it shipped.)

**Found:** CMX-280 rework round 4 (2026-08-14), PR #351 — `_fake_show`/
`_fake_list_and_show` in `tests/test_memory_slice_budget.py` matched only on
`cmd[:3]` (plus `cmd[3]` as a unit-name lookup key), so the judge's `MemoryMax` →
`MemoryHigh` and `--type=slice` → `--type=service` mutations in `chela/memcap.py` both
left every `live_bound()` test green — the fakes handed back fixture data keyed on the
unit name regardless of which systemd property or unit type was actually requested.
3098 tests stayed green. Closed by asserting the trailing `-p` flags and the
`--type=slice` filter inside the fakes themselves.
