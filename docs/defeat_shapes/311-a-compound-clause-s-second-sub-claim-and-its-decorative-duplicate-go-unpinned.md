## 311. A compound clause's second sub-claim, and its decorative duplicate, go unpinned

**Assertion form:** round 1's fix for [310](310-a-sibling-renderer-s-changed-clause-has-no-assertion-of-its-own.md)
pinned the literal clause a fix rewrote — but a "closed" clause can still hide two more gaps
of the same shape underneath it:

1. **A compound clause names two independent facts; only one gets pinned.** The stale-install
   `Fix:` text says `it runs `claude plugin marketplace update <marketplace>` + `claude plugin
   update chela@<marketplace>` for every confirmed-installed copy` — two separate CLI verb
   calls joined by `+`. Round 1 added `"claude plugin marketplace update <marketplace>" in
   out` to the `chela plugin` renderer's test, closing the *first* verb there. It never added
   the matching assertion to `chela doctor`'s own test for the *same compound clause* in the
   sibling renderer, and never pinned the *second* verb (`claude plugin update
   chela@<marketplace>`) in either renderer. Two more silently-swappable words sat right next
   to the one word round 1 fixed.
2. **The message's load-bearing line duplicates its own decorative prose ([33](33-a-guarded-fragment-appears-twice-decorative-and-load-bearing.md)), one hop downstream of the clause round 1 pinned.** `_report_installed_plugin` says `chela update` twice: once in the sentence explaining *why* to run it, and once as the standalone `print("    chela update")` action line meant to be copy-pasted. Round 1's `"chela update" in out` assertion is satisfied by the prose alone — the actual command line an operator runs was never independently pinned.

**Mutation that defeats it:** in the file this round 1 already touched, change words the round
1 assertions never named:

```diff
# chela/runtime_truth.py — the doctor Finding's OWN copy of the compound clause,
# never pinned in tests/test_installed_plugin.py::test_doctor_ERRORs_when_the_installed_manifest_disagrees
- "plugin marketplace update <marketplace>` + `claude plugin update "
+ "plugin marketplace refresh <marketplace>` + `claude plugin update "
```

```diff
# chela/main.py — the SECOND verb in the by-hand fallback, never pinned even though the
# first verb was pinned by round 1's fix for shape 310
-               "`claude plugin update chela@<marketplace>`)")
+               "`claude plugin refresh chela@<marketplace>`)")
```

```diff
# chela/main.py — the action line itself, distinct from the prose sentence that also
# contains the phrase "chela update"
-         print("    chela update")
+         print("    chela self-update")
```

All three: file changes on disk, still parses, `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
stayed green (3218 passed, 0 failed) with each corruption in place, in a throwaway checkout of
this PR's head.

**Why this slips through even after a directly-adjacent fix:** closing shape 310 felt like
closing "the stale-install message" as a topic, because the diff touched the exact clause the
mutation named. But that clause is not atomic — it is a compound sentence naming two CLI
calls, rendered independently in two sibling functions, with a load-bearing action line
duplicating one of its own sub-phrases three lines below. Pinning the substring the LAST
mutation swapped does not imply the neighboring substrings in the same sentence, or the same
sentence's second appearance in a sibling renderer, are pinned too. Each fix round narrows the
gap to whatever the judge's mutation happened to name; the compound and duplicated structure
underneath keeps producing new, equally-plausible one-word swaps the existing assertions don't
distinguish.

**Guard form that survives:** when a clause names N independent facts (here: two CLI verb
calls), assert each fact's literal text separately, in EVERY renderer that repeats the clause
— not just the one the last mutation happened to hit. When a phrase appears more than once in
the same output (prose explanation + standalone action line), pin the specific occurrence that
is actually meant to be acted on (e.g. `"\n    chela update\n" in out`, anchored to its own
line), not just the phrase anywhere in the blob. Before trusting either assertion, hand-swap
one word in each sub-claim and each occurrence independently and confirm the test goes red for
each swap on its own — not just for the one your fix round happens to be reacting to.

**Found:** `chela/runtime_truth.py`'s `_installed_report` and `chela/main.py`'s
`_report_installed_plugin` / `cmd_plugin` (CMX-310, PR #386, rework round 2) — three
independent one-word swaps (doctor's first verb, main.py's second verb, main.py's action
line) all survived a test suite that round 1 had *just* strengthened for the exact same
message. Closed by pinning both CLI verbs in both renderers' tests, and by pinning the
action line's own line (`"\n    chela update\n"`) instead of the phrase anywhere in the
printout.
