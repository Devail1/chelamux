## 348. A three-conjunct suppression gate had only one conjunct independently pinned

**Assertion form:** a suppression gate for a synthetic tool-injected record is written as
three conjuncts — `source_id and data.get("isMeta") and skill_info is not None and
skill_info.tool_name == "Skill"` (the second and later clauses live inside the block the
first two conjuncts open). The suite's only negative control for the third/fourth conjuncts
used a fixture where `source_id` resolved to **nothing at all** in `pending`
(`skill_info is None`) — a state that satisfies `is not None: False` regardless of whether the
`tool_name == "Skill"` comparison after it is even present. One fixture was doing double duty
as a stand-in for two structurally different "this record must not be swallowed" claims, and
only proved the weaker one.

**Mutation that defeats it:**
1. `if source_id and data.get("isMeta"):` → `if source_id:` — drops the `isMeta` conjunct
   entirely. No fixture ever sent a non-meta record whose `sourceToolUseID` *does* resolve to
   a real pending `Skill` entry, so nothing distinguished "isMeta was checked" from "isMeta was
   never checked at all."
2. `if skill_info is not None and skill_info.tool_name == "Skill":` →
   `if skill_info is not None:` — drops the `tool_name` conjunct. The suite's only "not
   swallowed" fixture used an id that resolves to *nothing* in `pending`
   (`skill_info is None`), so it never exercised the branch where `skill_info` is a **real,
   non-`Skill`** pending entry (e.g. a `Bash` tool_use) — the actual case the removed
   comparison exists to reject.

Both mutations left the suite fully green (3758/3758) because the one fixture standing in for
"must not be swallowed" only ever drove the `is None` path, not the `resolves to a different
tool` path, and no fixture drove `isMeta=False` with a `source_id` that resolves at all.

**Guard form that survives:** for a gate chaining **N** conjuncts, each conjunct needs its own
fixture that holds every *other* conjunct at the value that would let a record through, and
flips only that one conjunct to the value the gate is supposed to reject on — not a single
fixture reused across conjuncts. Concretely, this round added:
- a fixture with `isMeta=False` but a `source_id` resolving to a real pending `Skill` entry
  (kills mutation 1 — the record must relay normally, not swallow),
- a fixture with `skill_info` resolving to a real, non-`Skill` pending entry, e.g. `Bash`
  (kills mutation 2 — must fall through to normal relay), and
- for completeness, a fixture with `skill_info is None` (the originating tool_use fell outside
  the read window — see below) kept as its own case rather than conflated with either of the
  above.

**A fourth, previously-unguarded population found in the same review:** the two judge
mutations above concern *rejecting* records that shouldn't be swallowed. A related but
distinct gap — found by re-driving the same gate against a lost-`pending` scenario rather than
by mutation — is a conjunct that should ALSO suppress but didn't: `skill_info is None` (the
`Skill` tool_use fell outside the read window, e.g. the transcript monitor skipped to EOF on a
large file) was, before this round, treated the same as "not a skill body" and relayed
**in full** — up to the original 257KB dump this PR exists to prevent. `isMeta` +
`sourceToolUseID` together already identify the record as tool-injected synthetic content
regardless of whether the specific originating tool_use is still resolvable, so the fix folds
`skill_info is None` into the suppress branch (`skill_info is None or skill_info.tool_name ==
"Skill"`) rather than the relay branch. This is the mirror lesson of the two mutations above:
a compound gate needs a fixture per conjunct in *both* directions — proving the conjunct
rejects what it should, and proving the surrounding logic doesn't accidentally relay what a
missing/degraded input should still suppress.

**Found:** CMX-348 rework round 1 (2026-09-09), PR #455. `chela/telegram/parser.py`,
`tests/test_telegram_parser.py`.
