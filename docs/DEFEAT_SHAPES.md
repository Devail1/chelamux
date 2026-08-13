# Defeat shapes — a catalog of guards that look like they work and don't

⚖️ [The judge](../chela/judge.py) exists because a passing suite is not proof: a guard can
be written in a shape where the invariant it claims to protect can be **corrupted** —
deleted, inverted, dead-coded, unwired — and the suite stays green anyway. Every shape below
was found *live*, by a judge round or a human review, on this repo. Before this file existed
that knowledge lived only in the comment above the one test that closed it — reachable to
someone already reading that file, and nobody else. The person writing the next dispatch
brief, or the agent designing the next guard, walked into the same trap a second time because
the first fix never left its test file.

**This is a catalog of measured defeats, not a checklist someone imagined.** Every shape below
names the PR or test that found it.

## How this file grows

- **Writing a guard?** Check the shape you're about to write against the list below *before*
  you write it — most of these look completely reasonable until you ask "what corruption
  would this miss?"
- **Reworking a `SURVIVED` verdict?** The judge names the guard and the mutation that defeated
  it (see `chela judge`'s block comment). If that shape isn't listed here, add a section for
  it as part of the same fix — the judge itself never commits to this repo (its checkout is a
  throwaway detached copy, deleted when it finishes), so the agent doing the rework is the one
  with a branch to put the new entry on.
- Each entry: the **assertion form** (how the guard was written), the **mutation that defeats
  it** (what corruption slips through), and the **guard form that survives** (how to write it
  so the same corruption goes red).

---

## 1. Presence/substring assertion defeated by dead-coding

**Assertion form:** the guard regex/string-matches the *source text* of a function body
(`assert.match(fnSource, /w.textContent = s.word/)`) instead of driving the function and
reading back what it actually did.

**Mutation that defeats it:** wrap the statement in dead code —
`if (false && w) w.textContent = s.word;`. The exact substring `w.textContent = s.word` is
still sitting in the file, byte-identical, so the source-text match still passes. The
statement never runs.

**Guard form that survives:** drive the REAL function through a REAL state transition and
read the value back off the actual rendered node (or return value) — never off the source.

**Found:** `tests/wallnav.test.mjs` tests 12b/12c (CMX-230) — `tests/dashboard_scale_nav_a11y.test.mjs`'s
GUARD 3a only source-matched `_applyWallTileFrame`'s repaint statements; dead-coding either
one left the regex green. 12b/12c instead flip a live `session_status` and assert the
`.gs-state-word`/`.gs-state-glyph` text actually changed on the DOM node.

---

## 2. Fixture parked on a default value

**Assertion form:** the guard checks a value while the fixture sits at whatever default it
was initialized to — a value that never crosses the threshold the guarded code reacts to.

**Mutation that defeats it:** delete or invert the threshold-crossing logic entirely. Nothing
in the test ever drives the fixture across the boundary, so the branch that got deleted was
never exercised in the first place — the suite can't tell the difference.

**Guard form that survives:** move the fixture's value **across** the exact threshold the code
is supposed to react to, and assert the reaction (a class added, a message changed) — not just
that the value round-trips at its resting default.

**Found:** `tests/wallnav.test.mjs` test 15c (CMX-230 round 7) — test 15 only ever polled at
`used_pct: 42` (`ctxLevel` stays `'ok'`, no severity class at all). Wrapping the
`ctxChip.className = …` assignment in `if (false)` left the chip painted with whatever class
it started with, and nothing in the suite noticed because nothing polled past 80%. 15c polls
at `used_pct: 85` — across the real `ctx-danger` threshold — and reads the class back off the
node.

---

## 3. Positive-case-only mount (never mounts the OFF state)

**Assertion form:** the guard only ever drives the component INTO its "on"/enabled state and
asserts something about that state. It never drives it back OFF.

**Mutation that defeats it:** remove the code path that's supposed to turn things back off (a
class revert, a chip hide, a `hidden = true`). Nothing regresses, because the OFF transition
is never exercised — every assertion in the file only ever reads the ON state.

**Guard form that survives:** after asserting the ON state, drive the fixture back to OFF and
assert the negative too — the class is gone, the chip is hidden again, the text reverted.

**Found:** every state-transition test in `tests/wallnav.test.mjs`'s 12/12b/12c/12d series ends
with `delete AGENTS[0].session_status; await terminals.termTick();` followed by an assertion
that the dot/word/glyph/pill actually reverted to idle — not just that it correctly turned
"working" once.

---

## 4. Compound mutation proves the pair, not either half

**Assertion form:** one experiment corrupts TWO independent things at once (for example, a
colourblind cue's glyph *and* its hue, corrupted together in a single mutation).

**Mutation that defeats it:** this shape is about the *experiment's own design*, not the
production code — a judge (or a self-check) that only ever tests both halves bundled together
cannot tell whether either one, alone, is actually guarded. One half can be pure decoration
and the compound experiment would still report KILLED, because the other half alone was
enough to trip the suite.

**Guard form that survives:** one experiment per independent guard. If a cue is glyph-and-hue,
write two `{before, after}` mutations — glyph alone, hue alone — so each is judged on its own,
discretely, exactly the way `chela/judge.py`'s own module docstring frames the colourblind
`chip()` guard (`tests/test_judge.py`'s `GUARD_PY`): "a glyph AND a hue. Hue alone is invisible
to a red-weak eye" — the corollary is that a test proving the *pair* survives corruption is not
proof either half does.

---

## 5. Asserting a source constant instead of the rendered value

**Assertion form:** the guard checks a value pulled from source — a constant re-imported, a
function's mere existence, a template literal read out of the `.js` file — rather than what
actually got rendered, wired, or POSTed at runtime.

**Mutation that defeats it:** sever the wiring that's supposed to CONSUME the constant (revert
a `chela.applyUpdate()` production call-site to `onclick="void 0"`). The constant itself is
untouched, so a check against the constant — or against `applyUpdate` merely being a defined
function — stays green even though nothing on the page calls it anymore.

**Guard form that survives:** read the value back from the RENDERED artifact — the actual
`onclick` attribute on the actual button node, the actual POST body, the actual DOM text —
never from re-reading the source that's supposed to produce it.

**Found:** `tests/settings_update.test.mjs:160-165` — every earlier assertion in that file
reads `btn.disabled` / `row.textContent` (how the control *looks*), and the judge corrupted
`onclick="chela.applyUpdate()"` to `onclick="void 0"` with the whole suite staying green. The
fix asserts `btn.getAttribute('onclick')` matches `/chela\.applyUpdate\(\)/` directly — the
rendered wiring, not the control's appearance.

---

## 6. Coverage resting on a coincidence in production data

**Assertion form:** a guard passes today because of some incidental property of the current
fixture or realistic production data — a list that happens to stay short, IDs that happen to
stay unique — never because the code enforces it.

**Mutation that defeats it:** remove the actual enforcement (a cap, a warning, a dedup). The
suite stays green because nothing in the fixtures ever exercises the removed path — realistic
data just never reaches the boundary the enforcement exists for.

**Guard form that survives:** construct fixture data that deliberately breaks the coincidence
— push past the cap, force a duplicate — so the guard is exercised regardless of what today's
data happens to look like.

**Found:** `chela/sessions.py`'s `_MAX_CHILDREN` cap (CMX-210/CMX-211) — a real process almost
never has more than 32 children, so a fixture built from realistic data would never reach the
truncation path at all. `tests/test_sessions_proc_shim.py`'s
`test_sh_children_beyond_the_cap_warns_out_loud` / `test_proc_children_beyond_the_cap_warns_out_loud`
deliberately build a fixture `_MAX_CHILDREN + 5` children deep to force the cap, rather than
trusting that production data would ever get there on its own.

---

## 7. Two callers, one guarded (the wiring your fixture happens to reach)

**Assertion form:** a test drives a function through *one* of its call sites and asserts the
right thing happens. The assertion is real, the fixture is honest, and the guard genuinely
fails when that path breaks.

**Mutation that defeats it:** break the function at a *different* call site. Nothing notices,
because no fixture in the suite ever drives that one. The reviewer reads a passing test named
after the behaviour and reasonably concludes the behaviour is covered — when what is covered is
one route to it.

**Guard form that survives:** enumerate the call sites (`git grep` the symbol — this is a
question with a countable answer, unlike a CSS-property space) and drive *each* one. If N
call sites exist, the suite needs N wiring tests, and the test names should say which route
each covers.

**Found:** twice in one day, 2026-08-13.
- `_syncSidebarActive` (CMX-257) has exactly two callers — `selectView` and `showAgentDetail`.
  Rounds 19-21 guarded the first exhaustively (class set, cue painted, row routed). The second
  hardcodes a *demoted* view id (`view === 'agent-detail' ? 'agents' : view`) and nothing drove
  it: blanking that literal left the sidebar with no lit row on every agent drill-in, with 3012
  tests green. Closed in round 23 by driving the real `chela.selectAgent` path.
- `latest_required_mutations` (CMX-269) is wired into the rework brief at two render paths —
  `_respawn_rework` and `_renudge_prompt`. Both new prompt tests drove `tick`, which reaches
  only one per run state; dropping the argument from the other stayed green.

⭐ The judge caught the second one by proposing **a separate wiring experiment per call site
rather than guessing which was covered** — which is also the cheapest way to write the guard.

---

## 8. A differential guard that cancels

**Assertion form:** the test mounts two fixtures — the "feature on" case and a "base" case —
and asserts they *differ* (or match). Comparing against a baseline feels more robust than a
bare literal, and for a while it is.

**Mutation that defeats it:** apply the regression somewhere that moves **both** fixtures
equally. The difference is unchanged, so the assertion holds no matter how bad the absolute
value gets. A differential is blind, by construction, to anything in the common mode.

**Guard form that survives:** assert the **absolute** resolved value, not a diff — "this
resolves to `0px`", not "this resolves to the same thing the base fixture does". Keep the
second fixture as a *control* if it aids the diagnosis, but do not let it carry the assertion.

**Found:** CMX-268 round 1 (2026-08-13). The airy-density revert was guarded by comparing an
`airy` fixture's resolved padding against a no-class `base` fixture mounted from the same
stylesheet. The judge added horizontal padding to `#term-stage` **ungated** — the natural shape
of a real regression, since the gating class no longer exists — which widened both fixtures
identically. The differential held, the wall stopped filling its stage, and 3012 tests stayed
green. Round 2 replaced it with absolute assertions (`paddingLeft === '0px'`,
`maxWidth === 'none'`).

⚠️ Related but distinct from shape 6: there the coverage rested on a coincidence in the *data*;
here it rests on a property of the *comparison*.

---

## 9. A behavior-changing fix shipped with no guard at all

**Assertion form:** none. The PR states plainly that it adds no test or guard — "this is a
production script fix" — and the suite is green because nothing in it was ever pinned to the
invariant the fix introduces.

**Mutation that defeats it:** revert the fix verbatim. With nothing asserting the new
behavior, a one-line revert to the exact pre-fix code is indistinguishable from the fix
itself — the whole suite, unrelated to the change, stays green.

**Guard form that survives:** when the fix is "stop guessing X, ask the real source of truth
for X instead," a single guard rarely closes the whole gap on its own — read whichever of the
two applies:
- If the "real source of truth" can be called on its own (a function, a `--print-X` mode),
  a *behavioral* test can drive it directly and prove its output has a property a guess could
  never have. Cheap, but only proves the function is honest — not that production code still
  calls it. See shape 7 for that half.
- A *static* exact-line match on the call site closes the shape-7 gap the behavioral test
  leaves — see shape 7 for when a source-text match is the strong form instead of the weak
  one shape 1 warns about.

**Found:** CMX-275 rework round 1 (2026-08-13), PR #345. `scripts/smoke-fresh-install.sh`'s
dashboard port picker changed from a blind `$(( 20000 + (RANDOM % 20000) ))` guess to a real
`bind(('127.0.0.1', 0))` kernel probe, with "no test or guard was added or changed" stated in
the PR body. The judge reverted the diff in a throwaway checkout and 3027 tests, including
every other test in `tests/test_smoke_fresh_install.py`, stayed green — nothing anywhere
checked where `$DASH_PORT` actually came from, only that some dashboard eventually answered
200. Round 1 factored the probe into `pick_free_port()`, exposed it via a `--print-port` fast
path, and paired a behavioral test (repeated samples must include at least one port outside
the `[20000, 40000)` band a blind guess is confined to) with a static exact-line match on the
production `DASH_PORT=$(pick_free_port)` call site (shape 7: the behavioral test alone
doesn't notice that specific line reverted while `pick_free_port()` itself stays honest). The
structure (a real seam plus a paired behavioral+static test) was the right shape, but round 2
defeated *both* halves without touching the fix — see shape 10, which is about the specific
way "the kernel was asked" turned out to be a proxy no band or source match could pin down.
`pick_free_port()`/`--print-port` were kept; the two tests were replaced with a declared
`NOT GUARDED`.

---

## 10. A range/band check as a proxy for "how the value was produced"

**Assertion form:** the guard asserts a produced value falls inside (or outside) a specific
numeric band, as a stand-in for a claim about *mechanism* — "this came from asking the kernel,
not from arithmetic" — rather than observing the mechanism directly. A paired "wiring" half
source-matches the one call site that's supposed to feed the value through.

**Mutation that defeats it:** two independent mutations, both against the same target.
- The band check is defeated by generating the arithmetic guess from a *different* band than
  the one the test happens to check. It's still a guess, still not the kernel — the mutation
  just moved to a part of the number line the test wasn't looking at. Any fixed band the test
  picks, the next mutation can dodge; there is no band exhaustive enough to close this, short
  of the entire feasible port space.
- The paired source match is defeated by leaving the matched line in place, unmodified, and
  *shadowing* its result on the very next line — a pattern this same script already uses
  legitimately elsewhere (a retry path re-picking a port after occupying the first one), so
  it isn't even an unusual shape to write.

**Why this is a distinct shape from 5, 6, and 9:** shape 5 is about reading a *constant* off
source instead of a rendered value; shape 6 is coverage resting on a coincidence in the data;
shape 9 is no guard at all. This shape is subtler than any of those: a real seam exists (the
production function is directly callable), the test drives it hundreds of times, and it
genuinely fails against the first mutation tried. It looks, and mostly is, the "guard form
that survives" shape 9 itself prescribes — right up until a second experiment targets the
*specific number range* the assertion happens to check, rather than the code path.

**The deeper problem, and why "guard form that survives" isn't a fix here:** the property
being claimed — "the kernel was asked" — is a mechanism, not an outcome. A mechanism has no
observable trace in the return value alone; a kernel-assigned port and a well-guessed one are
bit-for-bit indistinguishable numbers. The *only* outcome where the two mechanisms provably
differ is **contention** (something else already holds the port a guess would have picked) —
and a unit test, run on an otherwise-idle box, does not reproduce contention. Widening the
band the test checks doesn't change this; it only raises the number of mutations needed to
find an unchecked band, the same treadmill shape 1's "just pick a different dead-code wrapper"
represents for source matches.

**Guard form that survives:** stop looking for a wider proxy and
ask whether the property is observable at all under the constraints a test can actually
create (no root, no exhausting the OS's whole ephemeral range, no reliably-reproducible
contention). If it isn't, **declare `NOT GUARDED`**: name exactly what's unprotected, why
(the mechanisms are indistinguishable outside contention), and what covers the fix instead
(a one-line, self-evidently correct change, and/or the original bug report — here, a real CI
flake under contention — that already proved the old code wrong once). A declared gap that
says this beats a third band nobody can prove is the last one.

**Found:** CMX-275 rework round 2 (2026-08-13), PR #345 — both halves of the round-1 WIRING
guard for `scripts/smoke-fresh-install.sh`'s `pick_free_port()` (see shape 9) survived the
judge's mutations: the behavioral test's band check was defeated by a guess drawn from
`[40000, 60000)` instead of `[20000, 40000)`, and the static source match was defeated by
leaving `DASH_PORT=$(pick_free_port)` in place and overriding `$DASH_PORT` on the next line.
Resolved round 3 by declaring the gap `NOT GUARDED` in `tests/test_smoke_fresh_install.py`
rather than writing a third proxy.

---

## 11. Substring assertions on a nested payload never pin its wrapping envelope

**Assertion form:** the guard builds a structured payload (`{"experiments": [...]}`) and only
ever asserts substrings that live *inside* the inner list — a field name, a value, a piece of
surrounding prose — never anything that depends on the outer envelope actually being there.

**Mutation that defeats it:** strip the envelope and serialize the inner list directly
(`json.dumps(mutations)` instead of `json.dumps({"experiments": mutations})`). Every
substring the guard checks (`'"guard": "..."'`, `'"file": "..."'`, `'"before": ...'`, the
surrounding instructional text) is still a substring of the un-enveloped JSON, so the suite
stays green — even though the payload a downstream reader expects (a JSON *object* with an
`experiments` key) no longer parses as one. The concrete failure lands one step later, outside
the test: `judge.load_experiments` rejects a bare array with "must be a JSON object with an
`experiments` list", so an agent that copies the (silently wrong) rendered block verbatim, as
instructed, spends a round on a formatting error the brief itself caused.

**Guard form that survives:** assert on the structural marker that only the correct envelope
produces — e.g. `'"experiments": [' in rendered` — not just on substrings that would survive
either shape.

**Found:** `chela/dispatcher.py`'s `_required_mutations_section` (CMX-269 rework round 5) —
`test_the_rework_prompt_carries_the_REQUIRED_MUTATION_SET_as_a_copy_pasteable_JSON_block`
asserted `'"guard": "the glyph cue"'`, `'"file": "guard.py"'` and similar field-level
substrings, none of which distinguish `json.dumps({"experiments": mutations})` from
`json.dumps(mutations)`. Fixed by adding `'"experiments": [' in prompts[0]`.

---

## 12. A "stop, don't fall through" rule untested because every fixture has the property everywhere

**Assertion form:** a function is supposed to STOP at the first (or most recent) matching
entry in a sequence and return based on that entry alone — never falling through to consult
an older entry, even when the matching entry itself carries nothing useful. Every test fixture
that exercises the loop gives *every* candidate entry the same shape (all carry the payload,
or only one entry exists at all).

**Mutation that defeats it:** change `return X` (unconditional, first match) to `if <X is
non-empty/present>: return X` — i.e. keep scanning past a matching-but-empty entry instead of
stopping there. Every existing test either has a single relevant entry, or has multiple
entries that all carry the payload, so "stop at the first match" and "keep scanning until you
find a match with the payload" produce identical results on every fixture in the suite.

**Guard form that survives:** construct a fixture where the *most recent* matching entry
deliberately lacks the property (empty/absent), while an *older* one has it — and assert the
function returns the empty/absent result, not the older entry's. This is the one shape a
same-value-everywhere fixture structurally cannot exercise.

**Found:** `chela/dispatcher.py`'s `latest_required_mutations` (CMX-269 rework round 5) — the
function already stopped correctly (`return [...] if isinstance(raw, list) else []` on the
first non-retry entry), but no test had a history where the *latest* substantive verdict
carried no `mutations` while an *earlier* one did, so a fall-through mutation
(`if isinstance(raw, list): return [...]`, otherwise keep looping) stayed green. Fixed by
`test_latest_required_mutations_stops_at_the_latest_verdict_even_when_it_carries_no_findings`.

---

## 13. A per-item loop over a required SET tested with a set of exactly one

**Assertion form:** a function is supposed to check EVERY item in a list against some
condition and collect the ones that fail — but every fixture that drives it hands it a
list of length one.

**Mutation that defeats it:** change `continue` (skip this item, keep checking the rest of
the list) to `break` (stop checking entirely the moment one item passes). On a one-item
list these are identical — there is nothing left to check either way — so the suite stays
green. The concrete failure is asymmetric and worse than it looks: with two required items,
the agent re-tests the EASY one and the loop stops there, silently excusing the hard one it
never re-tested — which is exactly the "tested something easier instead of the case that
beat it" recurrence this checking function exists to catch, reached through the plural door
instead of the singular one every test exercises.

**Guard form that survives:** construct a fixture with at least TWO items in the required
set — one resubmitted (satisfied), one not — and assert the result names only the one that
was not resubmitted. A same-cardinality-everywhere fixture (every test uses length one)
structurally cannot tell `continue` from `break`.

**Found:** `chela/dispatcher.py`'s `_missing_required_mutations` (CMX-269 rework round 6) —
every fixture in `tests/test_dispatcher_task_finished.py` built its required set via
`_review_history_with_required(mutation)`, which always wraps exactly one mutation. Fixed by
`test_verify_self_check_flags_only_the_required_mutation_that_was_not_resubmitted`, which
hands the function two required mutations and asserts only the unsubmitted one is flagged.

---

## 14. A field pinned at one hop of a round-trip, untested at the next

**Assertion form:** a value is produced by one function, consumed by another, and the two
are separated by a serialize/render step in between. A test pins the value on the
*producing* side (e.g. an `as_dict()`/`to_dict()` method includes the field) and a separate
test pins it on the *consuming* side (a parser reads the field back correctly) — but nothing
drives an assertion through the hop in the middle, where the value is dumped into a rendered
text block for a human or another process to copy verbatim.

**Mutation that defeats it:** drop the field only at the render hop (e.g.
`json.dumps({k: v for k, v in m.items() if k != "field"})` instead of dumping the dict
as-is). Both the producing-side test and the consuming-side test still pass — neither of
them touches the render step — so the suite stays green even though the field never survives
the round trip in practice. The parser on the far end silently defaults the missing field
back to something else, changing behavior with no visible failure anywhere.

**Guard form that survives:** the fixture driving the render-step test must itself carry the
field with a distinctive, non-default value, and the assertion must check for that value's
literal serialized form in the rendered output — not just for OTHER fields the render step
also happens to preserve.

**Found:** `chela/dispatcher.py`'s `_required_mutations_section` (CMX-269 rework round 6) —
`Experiment.as_dict` was pinned to emit `kind` (round 2), and `judge.Experiment.parse` reads
it back, but the render-step tests
(`test_the_rework_prompt_carries_the_REQUIRED_MUTATION_SET_as_a_copy_pasteable_JSON_block`,
`test_a_re_nudged_rework_ALSO_carries_the_REQUIRED_MUTATION_SET`) used a fixture dict with no
`kind` key at all, so a render that stripped `kind` was invisible to both. Fixed by adding
`"kind": "wiring"` to each fixture and asserting `'"kind": "wiring"'` appears in the rendered
prompt.

---

## 15. A list rendered verbatim, tested with a list of exactly one — the render-side mirror of shape 13

**Assertion form:** the same shape as #11 (a required SET tested with a set of length one) —
but on the OTHER side of a check/render pair. Shape 11 was the *enforcement* side deciding
which items in the set are satisfied; this is the *render* side deciding which items in the
same set reach the agent's brief as copy-pasteable data. A one-item fixture cannot
distinguish "dump the whole list" from "dump only the first item" — `mutations` and
`mutations[:1]` produce byte-identical output when `len(mutations) == 1`.

**Mutation that defeats it:** truncate the list before serializing it —
`json.dumps({"experiments": mutations[:1]}, indent=2)` instead of `mutations`. On a one-item
fixture this is invisible. The concrete failure is worse than a silent gap because the two
sides of the pair are coupled: the judge blocks with two survivors, the brief renders only
the first, the agent copies the JSON exactly as instructed, and shape 13's own fix —
correctly — flags the second as missing. The agent is then refused for omitting a mutation
it was never shown, with no way to discover what it is: an unescapable refuse-loop produced
by fixing one side of a pair and not the other.

**Guard form that survives:** construct a fixture with at least TWO items in the required
set and assert that BOTH appear in the rendered output by their distinguishing fields — not
just that "a" required-mutation section exists, and not just fields the first item alone
would already satisfy.

**Found:** `chela/dispatcher.py`'s `_required_mutations_section`, at both call sites that
render it (CMX-269 rework round 7) — the same two tests fixed for shape 14
(`test_the_rework_prompt_carries_the_REQUIRED_MUTATION_SET_as_a_copy_pasteable_JSON_block`,
`test_a_re_nudged_rework_ALSO_carries_the_REQUIRED_MUTATION_SET`) still built their
`mutations` list with exactly one entry, so round 6's enforcement-side fix for shape 13 had
no render-side counterpart. Fixed by giving each fixture a second, distinct mutation and
asserting both survivors' `guard` and `file` values appear in the rendered prompt.

---

## 16. The same one-item fixture, independently, at every hop a list-shaped value passes through

**Assertion form:** a value that is a LIST travels through several functions on its way from
where it is produced to where it is finally acted on — extracted from a report, stored on a
row, read back, rendered into text, scanned against another list, printed. Shapes 13 and 15
each pin ONE hop of a chain like this. This shape is what happens when nobody asks the
question at the level of the whole chain: every hop was written assuming a list of length
one, every hop's test fixture independently happens to use a list of length one, and each
hop gets discovered and fixed on its own round, one at a time, because nothing forces the
question "does EVERY hop this value passes through make the same assumption?" to be asked
once, up front, for the whole pipeline.

**Mutation that defeats it:** truncate to `[:1]` at any hop not yet separately pinned.
Because each hop is independently guarded (or not) by its own fixture, fixing hop N tells
you nothing about hop N+1 — shape 13's fix (the enforcement-side scan) shipped in round 6 and
shape 15's fix (the render step) shipped in round 7, and FOUR more hops on the exact same
pipeline — the extraction from the judge's own report, the storage on the review row, the
submitted-side of the enforcement scan, and the final print loop — were still open in round
8, each because its own test suite's fixtures, built independently by different rounds,
happened to use a one-item list too.

**Guard form that survives:** don't fix hops as they're found one at a time. When a
list-shaped value is discovered to have this defect at ANY hop, walk the value's entire
journey — every function that receives it, stores it, or passes it on — and give every
fixture along the WHOLE chain a two-item list in the same pass, not just the hop the current
finding named.

**Found:** the REQUIRED MUTATION SET's seven-hop journey (CMX-269): `judge.judge_run`
extracting `blocking` from its own report (shape unfixed until round 8),
`dispatcher.request_changes` storing it on the review entry (unfixed until round 8),
`latest_required_mutations` reading it back (pinned from the start — every multi-item
fixture reaches it), `_required_mutations_section` rendering it into the brief (shape 15,
round 7), `_missing_required_mutations`'s required-side loop (shape 13, round 6), the same
function's submitted-side `submitted_keys` (unfixed until round 8), and
`main.cmd_task_finished`'s print loop (unfixed until round 8). Two rounds each closed one
hop; round 8 closed the remaining four in a single pass —
`test_the_REQUIRED_MUTATION_SET_carries_every_survivor_not_just_the_first` (one test,
closing the source and storage hops together, since both sit on the same call chain),
`test_verify_self_check_clears_when_the_required_mutation_is_resubmitted_second_in_the_list`,
and `test_cmd_task_finished_prints_every_missing_required_mutation_not_just_the_first`.

---

## 17. A large green suite as false comfort for a claim that has zero guard of its own

**Assertion form:** a PR's own description (or test-plan section) states a specific
behavioral claim — often "the default X moves from A to B" — that depends on one or more
small, easy-to-miss literals (a fallback return value, a pre-set CSS class in a template, a
`let` initializer). The PR ships alongside a large, genuinely-passing test suite covering
the surrounding feature, and that suite's size and greenness reads as coverage — but none of
its tests ever reads back the actual runtime/rendered consequence of the specific literal
the claim depends on. This is subtler than shape 9 ("no guard at all, and the PR says so"):
here the PR believes it shipped tests for the change, and did — just not for this claim.

**Mutation that defeats it:** revert any one of the claim's load-bearing literals to its
pre-change value, in isolation. Every test in the surrounding (large, real, honest) suite
was written against the FEATURE, not against this specific default, so none of them ever
drives execution through the literal's actual consequence — the suite's size is irrelevant
to whether this one fact is pinned.

**Guard form that survives:** for every literal a PR's own claim names as load-bearing,
write a guard that reads its RENDERED/runtime consequence (a live DOM node's class list, an
imported module's live binding, a downstream render that only fires if the value is right)
— never a re-parse of the literal itself — and manually revert the literal once to confirm
the new guard actually goes red before trusting it. A claim stated only in prose (a PR
description, a code comment, a test-plan bullet) is not evidence it was ever tested; treat
it as a checklist of guards still owed.

**Found:** CMX-279 rework round 1 (2026-08-13), PR #350 — three independent literals all
backed the PR's own claim that "the default view (when the wall is off) moves from Agents to
Work": util.js's `let currentTab = 'work';` initializer, index.html's pre-set
`class="panel active"` on `#panel-work`, and nav.js's `_agentDetailBackView()` fallback.
3059 tests passed, none of which ever booted `main.js` with a genuine terminals-off
bootstrap and read back `currentTab`, `#panel-work`'s classList, or the agent-detail "←
Back" link's actual target. The judge reverted each literal independently, in a throwaway
checkout, and the full 3059-test suite stayed green all three times. Closed by
`tests/dashboard_default_view.test.mjs` (a real terminals-off `main.js` boot reading the
live `currentTab` binding, the real `#panel-work` DOM node, and the `renderKanban()` paint
that only happens if the `currentTab` gate at `work.js:174` actually lets it through) plus
two new assertions extending `tests/sidebar.test.mjs`'s existing agent-detail drill-in tests
(covering both `_agentDetailBackView()` call sites — nav.js:560 and :608 — on the
terminals-on branch, the counterpart to the terminals-off branch the new file covers).

---

## 18. Coverage deleted alongside the feature it shared a *file* with

**Assertion form:** a PR deletes a whole view/feature and, with it, that view's test file —
reasonable, since the view's OWN code is gone too. But some of the production code the
deleted view used was never exclusive to it: another surviving surface (a shared renderer,
a shared helper) imports the same module and is explicitly called out — in the PR's own
summary or the surviving file's header comment — as proof that module "survives, still
used verbatim." The deleted test file, however, held the *only* guards on branches of that
shared module the surviving caller's own tests never happen to exercise (a fixture that only
ever used one of the module's two code paths). Deleting the file deletes those guards too,
silently — the suite's pass count doesn't even move, because nothing was left half-covered
in a way a diff of test *counts* would show.

**Mutation that defeats it:** corrupt the surviving module's unexercised branch (the one only
the deleted view's tests drove). Nothing in the remaining suite reaches it, so the corruption
ships clean — while the PR's own text claims that exact module "still works" for the
surviving caller.

**Guard form that survives:** when a PR deletes a test FILE (not just a test), list every
production symbol that file imported and tested, and for each one still referenced by
surviving code, check off that either (a) the deleted file's guards for the branches the
survivor actually exercises were re-homed into a surviving test file, or (b) an equivalent
guard already exists there. "The suite still passes at N tests" is not evidence — a file that
tested 8 branches of a 3-branch-shared, 5-branch-exclusive module and gets deleted whole
looks, in a pass-count diff, identical to a file that tested nothing the survivor needed.

**Found:** CMX-279 rework round 2 (2026-08-14), PR #350. `tests/knowledge_graph.test.mjs`
was deleted with the rest of the Knowledge view (CMX-279's five-view strip), but
`knowledge.js`'s `knMd`/`knInline` were kept — per the file's own header — because the Work
view's task-detail modal (`taskmodalmodel.js`/`taskmodal.js`) and `kanban.js`'s card titles
still call them verbatim. The surviving guard (`tests/taskmodal_model.test.mjs`'s exact-output
`briefHtml` test) only ever fed `knMd` a heading + an ORDERED (`1.`/`2.`) list + inline code —
no fixture anywhere contained a `-`/`*` bullet. The judge made a `-` run open `<ol class="kn-ol">`
while `closeList()` still emitted `</ul>` for it (mismatched tags on every bulleted brief in
the app) and the full suite — 3064 tests — stayed green. Closed by three new tests added
directly to `tests/taskmodal_model.test.mjs` (not a revived `knowledge_graph.test.mjs`, since
the Knowledge view itself is gone — the guard belongs with the surviving caller now) driving
`knMd` on a `-` run, a heading splitting a `-` run from a `1.` run, and a `-` run switching
directly into a `1.` run mid-document — restoring the three cases the deleted file's own guard
comments named.
