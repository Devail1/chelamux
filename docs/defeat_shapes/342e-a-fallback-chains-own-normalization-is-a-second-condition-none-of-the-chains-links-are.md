## 342e. A fallback chain's own normalization step is a *second* condition, distinct from every link in the chain — proving the links' relative order says nothing about whether the normalization runs at all

**Assertion form:** `who = (by or "").strip() or os.environ.get("USER") or
os.environ.get("USERNAME") or "unknown"` has two kinds of decision point that look like one
chain but are not: the **links** (`by` vs `$USER` vs `$USERNAME` vs `"unknown"` — which source
wins) and the **normalization** applied to the first link before it's tested (`.strip()` —
whether a non-empty-but-blank `by` counts as "given" at all). [[342|shape 342]]'s own guard-form
list already names "an explicit argument and each link of its own fallback chain" as a
candidate to check, and
`test_acknowledge_by_prefers_env_USER_over_USERNAME_when_both_are_set` (closing round 9 of
shape 342, then round 3/4 of this branch's [[342c|shape 342c]]/[[342d|shape 342d]]) does prove
the **links'** relative order — `$USER` before `$USERNAME`. But every fixture in the file that
reaches the fallback chain either omits `by` (`None`, which `.strip()` never touches since
`by or ""` already made it `""`) or passes a fully non-blank value — none ever passes a `by`
that is *present but blank after normalization*, so `(by or "").strip()` and `(by or "")` read
back identically everywhere: the `.strip()` call itself is unproven, even though the chain it
guards is fully proven.

**Mutation that defeats it:** drop the normalization from the first link only, leaving the
chain's link order untouched: `who = (by or "").strip() or os.environ.get("USER") or
os.environ.get("USERNAME") or "unknown"` becomes `who = (by or "") or os.environ.get("USER")
or os.environ.get("USERNAME") or "unknown"`. Every existing fixture stays green — the `$USER`-
vs-`$USERNAME` test never passes `by` at all, the "records the explicitly supplied by" test
passes a non-blank `by`, the "defaults to env user" and "falls back to unknown" tests both pass
no `by` — because none of them constructs the one input where `by` is truthy as a raw Python
string (`"   "`) but falsy after normalization (`""`). Under the mutation,
`chela judge ack-blocked-race run --by '   '` stamps `'   '` as the acknowledging actor in the
DB column, the event payload, the summary, and the return dict all at once, and the
`$USER`/`$USERNAME`/`"unknown"` chain the flag's own `--help` documents never runs.

**Guard form that survives:** for any fallback chain whose first link is normalized before
being tested (`.strip()`, a cast, a default-substitution — the same "gate vs. rendered content"
split [[342d|shape 342d]] catalogs, here applied to a *chain's entry condition* instead of an
f-string's render gate), the chain's link-order fixture and the normalization fixture are two
separate tests, not one: proving `$USER` beats `$USERNAME` requires a fixture where both are
set and `by` is absent; proving the normalization runs requires a *different* fixture where
`by` is present but reduces to empty under normalization, with a downstream env var set to
confirm the chain was actually reached rather than merely returning early. Neither fixture can
stand in for the other — the closed-321 lesson to add here is: enumerate the raw-vs-normalized
form of the **chain's own entry argument**, not only the chain's later links, as one more
"pair that coincides in every fixture" candidate under [[342|shape 342]]'s general question.

**Why this needed its own entry, not folding into [[342d|shape 342d]]:** 342d's gate and
content are two views of the *same already-known* value inside one rendering step (the note
that gets displayed). This entry's two variables are a chain's *entry test* (`by` truthy?) and
a *separate, later* computation (`.strip()`) that changes what "truthy" even means for that
same variable, gating whether three further fallback links run at all — not a render, a branch
into otherwise-untested code paths ($USER, $USERNAME, "unknown"). The consequence is also
categorically worse: 342d's defeat produces a cosmetic dangling separator; this one silently
breaks the documented `--by` contract for every caller who passes a value that happens to be
blank (a shell variable expansion gone empty-but-quoted, a form field with only spaces).

**Found:** CMX-342 (PR #445), judge round 5, immediately after round 4 closed [[342d|shape
342d]]'s sibling gap on `note`/`clean_note`. `chela/dispatcher.py`'s `acknowledge_blocked_race`,
`who = (by or "").strip() or ...` line; the judge dropped `.strip()` in a throwaway checkout of
the PR head, and `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3700 passed) stayed green. Closed
by `test_acknowledge_by_whitespace_only_falls_through_to_the_env_chain`
(`tests/test_dispatcher_blocked_race_ack.py`), which acknowledges with `by="   "` and a real
`$USER` set, and asserts the resolved actor is the env value, not the raw whitespace.

**See also:** [[342|shape 342]] — the general "two quantities that coincide in every fixture"
family, whose own guard-form list named this exact candidate ("an explicit argument and each
link of its own fallback chain") before it was closed. [[342d|shape 342d]] — the nearest
sibling: a gate/content split on the *same* variable, one round earlier, on `note` instead of
`by`, inside a render rather than a chain's entry test.
