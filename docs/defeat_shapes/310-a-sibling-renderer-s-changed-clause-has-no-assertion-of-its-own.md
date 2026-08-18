## 310. A sibling renderer's changed clause has no assertion of its own

**Assertion form:** a fix rewrites the same "how to recover" instruction in two
near-identical renderers of the same event — `chela doctor`'s `_installed_report` Finding
body and `chela plugin`'s `_report_installed_plugin` printout both render a "the INSTALLED
plugin is stale, here's how to fix it" message on the same drift-detected branch, as two
separate functions with two separate format strings. The PR updates the test for only one of
the two renderers. Worse, the assertions added there pin *unrelated* fields of the same
message — `"INSTALLED" in body`, `"PermissionRequest" in body`, `"STARTUP" in body` — every
one of which is equally true of the pre-fix and post-fix wording, because none of them sit
inside the clause the fix actually changed. The clause that changed (`Fix: ... chela update
...` vs the old `Fix: ... /plugin uninstall ...`) has no assertion pinned to it anywhere in
that test, and the doctor-side sibling test has no update at all.

**Mutation that defeats it:** revert either renderer's changed clause back to its pre-PR
wording verbatim, or swap one word inside the still-correct new wording:

```diff
-                + "\n    Fix: `chela update` already refreshes this copy for you, "
-                "non-interactively — no uninstall/reinstall needed (it runs `claude "
-                "plugin marketplace update <marketplace>` + `claude plugin update "
-                "chela@<marketplace>` for every confirmed-installed copy). Run it, or do "
-                "those two calls by hand. Hooks are read at agent STARTUP — a running "
-                "agent keeps the stale ones until it is restarted.",
+                + "\n    Fix: `chela plugin`, then in Claude Code `/plugin uninstall "
+                "chela@chela` + `/plugin install chela@chela` to refresh that copy. "
+                "Hooks are read at agent STARTUP — a running agent keeps the stale ones "
+                "until it is restarted.",
```

```diff
-        print("  (or by hand: `claude plugin marketplace update <marketplace>` then "
+        print("  (or by hand: `claude plugin marketplace refresh <marketplace>` then "
```

The first mutation is a full revert of the doctor-side clause back to the pre-PR "manual
uninstall/reinstall" wording. No test in `tests/test_installed_plugin.py` asserted anything
about that clause, so nothing noticed. The second mutation swaps one word (`update` →
`refresh`) inside the `chela plugin` by-hand fallback line. That renderer's message DOES have
a test, but the assertions there — `"chela update" in out"` and `"/plugin uninstall" not in
out` — are both still true after the swap: neither one names the by-hand fallback line at
all, only the two sentences around it. Both mutations left `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q` green (3218 passed, 0 failed).

**Why this slips through even though a test exists:** the test author reasoned at the level
of "does this message still say roughly the right thing" and pinned whatever substrings were
easiest to spot in the surrounding Finding body (severity words, an already-guarded path,
already-guarded field names) rather than the literal span the diff actually rewrote. A test
that looks like it exercises "the stale-install message" can pass on a message that says
something different in the one place it actually changed, if every assertion in it targets
prose the fix left untouched. And updating one of two sibling renderers' tests reads as
"the feature has a test" even though the second renderer — the one nobody touched — has
none.

**Guard form that survives:** when a fix touches two sibling renderers of what is
conceptually "the same" message, each renderer needs its OWN assertion on the literal clause
it changed — testing one is not evidence for the other, even when the two functions are a
few lines apart and render near-identical text. And within one renderer's test, assert the
exact new phrase the fix introduces (`"no uninstall/reinstall needed" in body`, `"claude
plugin marketplace update <marketplace>" in out`) together with the absence of the old
wording (`"/plugin uninstall" not in body`) — not fields merely adjacent to the changed
clause that were already true of both the old and new versions. Before trusting the
assertion, hand-revert the exact clause the fix claims to have changed and confirm the test
goes red.

**Found:** `chela/runtime_truth.py`'s `_installed_report` and `chela/main.py`'s
`_report_installed_plugin` (CMX-310, PR #386, rework round 1) —
`tests/test_installed_plugin.py::test_doctor_ERRORs_when_the_installed_manifest_disagrees`
had no assertion at all on the doctor Finding's `Fix:` clause, and
`test_chela_plugin_names_the_cache_path_when_the_install_is_stale` asserted `"chela update"
in out` / `"/plugin uninstall" not in out`, neither of which distinguishes `claude plugin
marketplace update` from `claude plugin marketplace refresh` in the by-hand fallback line.
Both reverts, applied by the judge to a throwaway checkout of the PR's head, stayed green
against `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3218 passed, 0 failed). Closed by adding
`"chela update" in body`, `"no uninstall/reinstall needed" in body`, and `"/plugin uninstall"
not in body` to the doctor test, and `"claude plugin marketplace update <marketplace>" in
out` to the `chela plugin` test.

**Round 2 — the fix for round 1 was itself narrow in the same way:** closing the clause round
1 named left three more one-word swaps sitting right next to it, all in files round 1 had
*just* edited:

1. **A compound clause names two independent facts; only one got pinned.** The `Fix:` text
   says `it runs `claude plugin marketplace update <marketplace>` + `claude plugin update
   chela@<marketplace>` for every confirmed-installed copy` — two separate CLI verb calls
   joined by `+`. Round 1 added `"claude plugin marketplace update <marketplace>" in out` to
   the `chela plugin` renderer's test, closing the *first* verb there — but never added the
   matching assertion to `chela doctor`'s own test for the *same compound clause* in the
   sibling renderer, and never pinned the *second* verb
   (`claude plugin update chela@<marketplace>`) in either renderer.
2. **The message's load-bearing line duplicates its own decorative prose (see shape
   [33](33-a-guarded-fragment-appears-twice-decorative-and-load-bearing.md)), one hop
   downstream of the clause round 1 pinned.** `_report_installed_plugin` says `chela update`
   twice: once in the sentence explaining *why* to run it, once as the standalone
   `print("    chela update")` action line meant to be copy-pasted. Round 1's `"chela update"
   in out"` assertion is satisfied by the prose alone — the actual command line an operator
   runs was never independently pinned.

Round 2's mutations, all applied by the judge to a throwaway checkout of round 1's own fix,
still parsed and left `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` green (3218 passed, 0
failed):

```diff
# chela/runtime_truth.py — the doctor Finding's OWN copy of the compound clause, never
# pinned in test_doctor_ERRORs_when_the_installed_manifest_disagrees
- "plugin marketplace update <marketplace>` + `claude plugin update "
+ "plugin marketplace refresh <marketplace>` + `claude plugin update "
```

```diff
# chela/main.py — the SECOND verb in the by-hand fallback, never pinned even though the
# first verb was pinned by round 1
-               "`claude plugin update chela@<marketplace>`)")
+               "`claude plugin refresh chela@<marketplace>`)")
```

```diff
# chela/main.py — the action line itself, distinct from the prose sentence that also
# contains the phrase "chela update"
-         print("    chela update")
+         print("    chela self-update")
```

**Why round 1's fix didn't already cover this:** closing shape 310 felt like closing "the
stale-install message" as a topic, because the diff touched the exact clause the round-1
mutation named. But that clause is not atomic — it is a compound sentence naming two CLI
calls, rendered independently in two sibling functions, with a load-bearing action line
duplicating one of its own sub-phrases three lines below. Pinning the substring the last
mutation swapped does not imply the neighboring substrings in the same sentence, or that same
sentence's second appearance in a sibling renderer, are pinned too.

**Guard form that survives (updated):** when a clause names N independent facts (here: two
CLI verb calls), assert each fact's literal text separately, in EVERY renderer that repeats
the clause — not just the one the last mutation happened to hit. When a phrase appears more
than once in the same output (prose explanation + standalone action line), pin the specific
occurrence that is actually meant to be acted on (e.g. `"\n    chela update\n" in out`,
anchored to its own line), not just the phrase anywhere in the blob. Before trusting either
assertion, hand-swap one word in each sub-claim and each occurrence independently and confirm
the test goes red for each swap on its own — not just for the one your fix round happens to be
reacting to.

**Round 2 found:** (CMX-310, PR #386, rework round 2) — closed by adding
`"claude plugin marketplace update <marketplace>" in body` and
`"claude plugin update chela@<marketplace>" in body` to the doctor test, adding
`"claude plugin update chela@<marketplace>" in out` to the `chela plugin` test, and replacing
that test's `"chela update" in out` with `"\n    chela update\n" in out` so the action line is
pinned on its own line, not via the surrounding prose.

**Round 3 — the two CLI verbs were pinned; the sentence claiming they need no manual
round-trip was not.** Rounds 1 and 2 closed every literal CLI-verb substring in the compound
`Fix:` clause, in both renderers. What neither round touched is the *other* half of the same
sentence — the claim that `chela update` running those verbs makes the fix `non-interactively
... with no uninstall/reinstall needed`. `chela doctor`'s test pinned `"no uninstall/reinstall
needed" in body` back in round 1, but never `"non-interactively"`. `chela plugin`'s printout
repeats *both* phrases (`chela/main.py:960`), and its test asserted neither — only the two
verbs, the action line, and the absence of `/plugin uninstall`.

```diff
# chela/main.py — the `chela plugin` renderer's own copy of the same claim, never pinned by
# either round 1 or round 2 even though the verbs one line below it were
- "for you, non-interactively, with no uninstall/reinstall needed:")
+ "for you, interactively, with an uninstall/reinstall needed:")
```

```diff
# chela/runtime_truth.py — a second, independent claim in the SAME Fix clause: the refresh
# is scoped to CONFIRMED-installed copies, because `_plugin_marketplaces()` deliberately
# skips copies found only by the cache scan. Never pinned in any round.
- for every confirmed-installed copy). Run it, or do
+ for every installed copy chela can find). Run it, or do
```

Both mutations, applied by the judge to a throwaway checkout of round 2's fix, still parsed
and left `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` green (3227 passed, 0 failed).

**Why round 2's fix didn't already cover this:** the compound `Fix:` clause names more than
just "two CLI verbs" — it also names *how confidently* those verbs can be trusted (automatic,
no manual undo) and *how broad* their effect is (only confirmed-installed copies, not every
copy chela merely suspects). Round 2's guard form ("assert each fact's literal text
separately, in EVERY renderer that repeats the clause") was correct, but round 2 only applied
it to the facts the round-2 mutation itself had just hit — the CLI verbs — not to every
independent fact still living in the same sentence. A clause can have more independently
falsifiable sub-claims than the mutations seen so far have exercised; closing the ones a judge
round names is not the same as closing the clause.

**Guard form that survives (updated again):** before trusting a clause is fully pinned,
enumerate every independently-falsifiable claim in it — not just every CLI verb, every claim
of any kind (a scope qualifier, a manual-effort claim, a mode word like
interactive/non-interactive) — and assert each one's literal text on its own, in every
renderer that repeats it. A clause is not closed because the mutations you have seen so far
are all dead; it is closed when hand-swapping any single word in it, one at a time, turns the
suite red.

**Round 3 found:** (CMX-310, PR #386, rework round 3) — closed by adding
`"non-interactively" in out` and `"no uninstall/reinstall needed" in out` to the `chela
plugin` test, and `"confirmed-installed copy" in body` to the doctor test.

**Round 4 — round 3 closed the mode word in `chela plugin`'s test but not in `chela
doctor`'s, and left the sentence that MOTIVATES the by-hand fallback completely unpinned.**
Round 3's own closing note named the exact gap it was leaving open — "`chela doctor`'s test
pinned `"no uninstall/reinstall needed" in body`... but never `"non-interactively"`" — and
then closed that gap only in `chela plugin`'s test, not in `chela doctor`'s, even though
`chela/runtime_truth.py:1099` renders the identical claim. Separately, `chela plugin`'s
message opens with a sentence no round had touched: `chela will not write into Claude Code's
plugin cache directly (that copy is Claude Code's to manage) — but \`chela update\` already
refreshes it...`. That sentence is not decorative — it is the entire reason the by-hand
fallback (`claude plugin marketplace update` + `claude plugin update`) exists instead of
chela writing the cache file directly, and it names a real behavioral invariant (`chela
update` only ever shells out to `claude plugin ...`; it never touches the cache itself).

```diff
# chela/runtime_truth.py — the doctor Finding's OWN copy of the mode-word claim round 3
# pinned in the SIBLING renderer (chela/main.py) but not here, even though this file renders
# the identical clause
- "non-interactively — no uninstall/reinstall needed (it runs `claude "
+ "interactively — no uninstall/reinstall needed (it runs `claude "
```

```diff
# chela/main.py — the rationale sentence this PR wrote for the STALE-INSTALL message,
# never pinned in any round even though it explains why the by-hand fallback below it exists
- chela will not write into Claude Code's plugin cache directly (that "
+ chela may write into Claude Code's plugin cache directly (that "
```

Both mutations, applied by the judge to a throwaway checkout of round 3's fix, still parsed
and left `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` green (3227 passed, 0 failed).

**Why round 3's fix didn't already cover this:** round 3 diagnosed the gap correctly in
prose (its own closing note names the doctor-side `"non-interactively"` omission) but the
diff it shipped closed only the renderer the round-3 mutation had itself hit
(`chela/main.py`), not the sibling the note said was still open. This is the same failure
mode shape 310 exists to name — one renderer's test gets the fix, the sibling repeating the
identical clause does not — recurring a fourth time *inside the entry that already documents
it*, this time against the round's own stated diagnosis rather than against an unexamined
clause. A second, independent gap (the cache-ownership rationale sentence) had never been
named by any prior round at all: it sits one sentence above the compound `Fix:` clause every
prior round mined for sub-claims, in a part of the message none of them read as a
"falsifiable claim" because it reads as scene-setting rather than as an instruction.

**Guard form that survives (updated again):** when a prior round's own closing note names a
sibling renderer as still-open, treat that as a required assertion for THIS round, not
optional cleanup — the note is the guard spec. And re-read the entire message being fixed,
not just the clause the last few mutations have concentrated in: a rationale sentence
("why this fallback exists, and what chela promises never to touch") is exactly as
falsifiable as a CLI verb or a mode word, and mutation-hunting that only revisits the
compound `Fix:` clause misses it indefinitely.

**Round 4 found:** (CMX-310, PR #386, rework round 4) — closed by adding
`"non-interactively" in body` to the doctor test
(`test_doctor_ERRORs_when_the_installed_manifest_disagrees`), and `"chela will not write
into Claude Code's plugin cache directly" in out` to the `chela plugin` test
(`test_chela_plugin_names_the_cache_path_when_the_install_is_stale`).

**Round 5 — round 2 TIGHTENED an assertion into a more specific one instead of ADDING it,
so the occurrence the broad assertion used to cover went from pinned to unpinned in the same
edit.** Round 2's diff replaced `assert "chela update" in out` with `assert "\n    chela
update\n" in out` in the `chela plugin` test, to anchor the standalone copy-paste action line
on its own line (closing shape [33](33-a-guarded-fragment-appears-twice-decorative-and-load-bearing.md)
for that renderer). But `chela update` also appears a second time in the same printout — in
the prose sentence that CREDITS `chela update` with doing the refresh (`chela/main.py:959`:
`` ...but `chela update` already refreshes it for you...``). Round 2's replacement, rather
than addition, of the broad substring check meant that second occurrence lost its only
assertion. `chela doctor`'s sibling test still has the broad form
(`assert "chela update" in body`, unaffected by round 2 because round 2 only edited the
`chela plugin` test) — so the doctor-side prose sentence stayed pinned throughout, and only
the `chela plugin` side went dark, invisibly, three rounds before this one caught it.

```diff
# chela/main.py — the sentence that names WHICH command fixes the drift, one line above the
# copy-paste action line round 2 pinned on its own — never independently pinned since
- copy is Claude Code's to manage) — but `chela update` already refreshes it
+ copy is Claude Code's to manage) — but `chela plugin` already refreshes it
```

This mutation, applied by the judge to a throwaway checkout of round 4's fix, still parsed
and left `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` green (3227 passed, 0 failed) — because
`assert "\n    chela update\n" in out` does not match text with no leading newline+indent
(the prose sentence has `` `chela update` `` inline, not on its own line), so it is silent
about the swap, and nothing else in the test names that sentence.

**Why round 2's fix didn't already cover this:** round 2's own stated guard form was "when a
phrase appears more than once in the same output..., pin the specific occurrence that is
actually meant to be acted on" — correct advice for adding a *second*, more specific
assertion, but the diff round 2 shipped *replaced* the original broad assertion with the new
specific one instead of keeping both. Tightening a broad assertion into a narrower one
silently un-pins whatever the broad form used to cover that the narrow form doesn't — here,
the prose sentence — and nothing about a green suite distinguishes "this occurrence is still
covered by something else" from "this occurrence was covered by the assertion I just deleted
and now covered by nothing."

**Guard form that survives (updated again):** when the same fact-bearing phrase appears more
than once in one renderer's output (a prose sentence explaining *why*, plus a standalone
action line meant to be copy-pasted), each occurrence needs its OWN assertion, and a new,
more specific assertion for one occurrence must be ADDED alongside the existing broad one,
never substituted for it — unless every occurrence the broad assertion covered has first been
enumerated and each given its own replacement. Before deleting or narrowing any assertion,
enumerate every place in the actual printed output the string it matches occurs, and confirm
a replacement assertion still covers each one.

**Round 5 found:** (CMX-310, PR #386, rework round 5) — closed by adding
`` "chela update` already refreshes it" in out `` to the `chela plugin` test
(`test_chela_plugin_names_the_cache_path_when_the_install_is_stale`), alongside — not instead
of — the existing `"\n    chela update\n" in out` action-line assertion.
