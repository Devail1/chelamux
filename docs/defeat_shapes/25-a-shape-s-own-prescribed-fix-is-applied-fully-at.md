## 25. A shape's own prescribed fix is applied fully at one call site and only partially at its sibling

**Assertion form:** two call sites share the same shape-24 defect (a subprocess fake that
discards part of the argv it dispatches on). The fix lands as a *full* argv-equality
assertion at one site (`assert cmd[4:] == _SHOW_PROPS` for the `show` call) and as a
*membership* assertion — checking only that one known flag is present, not that the rest
of the argv matches — at the other (`assert "--type=slice" in cmd` for the `list-units`
call). Both read as "the fix for shape 24," and the fully-fixed sibling sitting right next
to it makes the partial one look reviewed rather than incomplete.

**Mutation that defeats it:** change any flag in the `list-units` call other than the one
membership checks — `--state=active` → `--state=inactive`. `--type=slice` is still
present, so the membership assertion still passes; the fake still fabricates the fixture's
active-looking units regardless of which state was actually requested. On a real box,
`--state=inactive` enumerates units that are NOT running (measured: zero units, versus one
for `--state=active`), so the function this feeds returns nothing and the capability it
powers reports "off" forever — the exact failure class shape 24 was written to catch, now
reintroduced through the one call site whose fix didn't generalize.

**Why this is distinct from shape 24:** shape 24 is "the fake doesn't look at the tail of
the argv at all." This is "the fake looks at *one flag* of the tail and treats that as
proof of the whole tail" — a fix that is *shaped* like a real fix (it does assert
something about the previously-invisible argv), so a reviewer (human or judge) scanning
for "was shape 24 addressed here" sees an assertion referencing `--type=slice` and moves
on, without checking whether that assertion covers `--state=active`,
`--no-legend`, `--plain`, and `--no-pager` too — the same shape-24 gap, just narrowed from
"all five flags" to "four of five flags."

**Guard form that survives:** when the same fake dispatches a command with more than one
significant flag, assert equality on the whole trailing slice (`cmd[3:] ==
[...]`), not membership of the one flag that happens to be top of mind while writing the
fix. When fixing a cataloged shape at N call sites, re-derive the guard from the
production argv at each site independently rather than pattern-matching the fix already
applied at a sibling site — the two sites can carry a different number of significant
flags, and a fix copied by feel tends to check only the flags the previous round's mutation
happened to target.

**Found:** CMX-280 rework round 5 (2026-08-14), PR #351 — round 4's own fix for shape 24
was applied to `_fake_show` as a full-argv assertion but to `_fake_list_and_show` as
`assert "--type=slice" in cmd`, leaving `--state=active` (and every other flag past
`--type=slice`) unguarded. `--state=active` → `--state=inactive` in `chela/memcap.py`
kept all 3103 tests green. Closed by asserting `cmd[3:]` against the full expected flag
list at the `list-units` call site.
