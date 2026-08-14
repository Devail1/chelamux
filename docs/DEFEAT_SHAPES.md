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

**Found again:** `tests/test_runtime_truth.py`'s `process.node_ipc_env` pair (CMX-281 rework
round 1, PR #352). `Finding.detail` is one string mixing a dynamically-rendered
`{k}={v!r}` clause (built from what `os.environ` actually held) with STATIC advice prose
that spells out both var names verbatim, unconditionally, for troubleshooting purposes
(`` `env -u NODE_CHANNEL_FD -u NODE_CHANNEL_SERIALIZATION_MODE` ``). The tests asserted
`"NODE_CHANNEL_FD" in findings[0].detail`, which the static prose alone satisfies — the
judge blanked the dynamic clause entirely and the suite stayed green. This is the same shape
as the `settings_update.test.mjs` case with a subtler disguise: it isn't a *separate* source
constant sitting elsewhere in the file, it's a compile-time-constant substring living inside
the very field that also carries the rendered value, so the two look, at a glance, like one
observation-derived string. The fix asserts the rendered `k=v!r` pair itself
(`"NODE_CHANNEL_FD='3'"`) and that the absent sibling's rendered pair is NOT present — pinned
to what the observation produced, not to any name the prose happens to mention.

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
- `judge_max_concurrent`'s floor=1 (CMX-278) has two entry paths into the value — the
  dashboard/`set_dispatch` *write* path, which `validate_dispatch` floors before it ever
  reaches storage, and the env-file *read* path (`CHELA_JUDGE_MAX_CONCURRENT`), which
  `dashboard_setting` resolves straight from `os.environ` and never runs through
  `validate_dispatch` at all. `test_judge_max_concurrent_floor_is_one_not_zero` drove only
  the write path; mutating the reader's own `max(1, ...)` to `max(0, ...)` — the last guard
  standing on the second path — left the suite green. Closed by a second test that sets the
  env var to `"0"` and asserts the reader still returns `1`.
- `knMd`/`knInline` (CMX-279 rework round 4, PR #350) has THREE call sites this PR edited —
  kanban.js:152 and taskmodal.js:156 (the title) were closed independently in round 3;
  taskmodal.js:116 (`_timelineHtml`'s `knMd(s.detail)` for the review-timeline body — the PR
  also edited this line, dropping the `'review.md'` argument) was never driven by any fixture,
  since every DOM test that reaches `openTaskModal` passes no `review_history`. Closed by a
  4th wiring test in `tests/taskmodal_render.test.mjs` that passes a `review_history` payload
  and reads the real `.task-modal-timeline-body` element back.
- `knMd`/`knInline` (CMX-279 rework round 5, PR #350) — recurred a SECOND time on the same
  symbol. `taskmodal.js:127` `_briefPane`'s `briefHtml(src)` call is a FOURTH call site (the
  header's own doc comment names it first, as the reason the module survives at all), and it
  was still unguarded going into round 5: both existing DOM tests in
  `tests/taskmodal_render.test.mjs` pass items with no `brief`/`body`/`raw`, so
  `briefSource(item)` resolves to `null` and `_briefPane` short-circuits to its "No brief
  recorded" note before ever reaching `briefHtml`. Closed by a 5th wiring test driving the real
  `openTaskModal` with an item carrying a markdown `brief` and reading `.task-modal-brief`
  back. The standing lesson from the second occurrence: counting call sites once is not
  enough — re-`git grep` the symbol every round a guard on it changes, since a caller added or
  edited in an earlier round of the SAME PR can still be the one nobody wired.

⭐ The judge caught the second one by proposing **a separate wiring experiment per call site
rather than guessing which was covered** — which is also the cheapest way to write the guard.
Two callers becomes N callers becomes "count them all, every round" — a shape doesn't stop
recurring just because it was closed once at a smaller N.

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

## 17. A syntactic-shape check on a live value instead of pinning what produced it

**Assertion form:** the guard checks that a value produced by a live mechanism (a clock, a
random source, an external call) has the right *shape* — length, character positions, a regex
— rather than pinning that the mechanism was actually invoked to produce it. `len(stamp) == 8
and stamp[2] == ":" and stamp[5] == ":"` reads exactly like "this is an HH:MM:SS timestamp"
and is satisfied identically by a real clock read and by a hardcoded literal.

**Mutation that defeats it:** replace the call to the live mechanism with a fixed value of the
same shape — `time.strftime("%H:%M:%S")` → `"00:00:00"`. Every shape assertion still passes;
the feature's entire claim (this is a *live* timestamp) is gone.

**Why this is distinct from shape 5 and shape 10:** shape 5 is about reading a constant off
source instead of the rendered/wired value — here the value genuinely is the rendered output,
not source. Shape 10 is a mechanism that is *unobservable outside contention* (a kernel-picked
port vs. a lucky guess are bit-for-bit identical, so no test can pin the mechanism directly,
only declare the gap). This shape is the easy case: the mechanism **is** directly observable —
the module holds the live source (`time`) as an attribute, so a test can monkeypatch it and
assert the exact value that flowed through, no declared gap required.

**Guard form that survives:** monkeypatch the live source itself
(`monkeypatch.setattr(hooks.time, "strftime", lambda fmt: "12:34:56")`) and assert the
rendered output equals the exact value the patched mechanism returned. This proves the code
path actually asks the mechanism, rather than merely producing something shaped like its
answer.

**Found:** CMX-277 rework round 2 (2026-08-14), PR #348 —
`test_timestamp_response_carries_the_proven_persistent_envelope`'s shape check
(`len(stamp) == 8 and stamp[2] == ":" and stamp[5] == ":"`) survived the judge's
`ts = time.strftime("%H:%M:%S")` → `ts = "00:00:00"` mutation. Closed by adding
`test_timestamp_response_asks_the_module_clock_not_a_fixed_string`, which monkeypatches
`hooks.time.strftime` and asserts the exact rendered `systemMessage`.

## 18. A monkeypatched stub that pins the mechanism was invoked, but discards which arguments it was invoked with

**Assertion form:** the guard from shape 17 — monkeypatch the live source and assert the exact
value the stub returned — proves the mechanism was *asked*, but the stub itself is written as
`lambda fmt: "12:34:56"`: it accepts `fmt` and throws it away. Any call, with any format string,
produces the same stubbed return value, so the assertion on that return value cannot tell two
different format strings apart.

**Mutation that defeats it:** change *what* is asked for, not *whether* it's asked —
`time.strftime("%H:%M:%S")` → `time.strftime("%d:%m:%y")`. Both calls still reach the
monkeypatched `strftime`, so the "mechanism was invoked" guard is untouched and still returns
the stubbed `"12:34:56"`. The rendered `systemMessage` is byte-identical either way, so the
shape-17 fix — which only ever inspects the return value — cannot distinguish a live *time*
stamp from a live *date* stamp. The docstring's claim (`HH:MM:SS`, local time) is now false for
a date-formatted string, and nothing failed.

**Why this is distinct from shape 17:** shape 17 is "was the mechanism invoked at all, versus a
hardcoded literal" — solved by monkeypatching the source and checking the output flowed through
it. This shape is one level deeper: *given* the mechanism was invoked, *which request* did the
code make of it? A stub that ignores its own arguments proves the former but is structurally
blind to the latter — the args never reach anything the assertion inspects.

**Guard form that survives:** capture the argument the code passed, not just the value the stub
handed back — `captured = []`; `monkeypatch.setattr(hooks.time, "strftime", lambda fmt:
captured.append(fmt) or "12:34:56")`; then assert `captured == ["%H:%M:%S"]` in addition to
asserting the rendered output. This pins the request, not just that a request happened.

**Found:** CMX-277 rework round 3 (2026-08-14), PR #348 — the judge's
`ts = time.strftime("%H:%M:%S")` → `ts = time.strftime("%d:%m:%y")` mutation survived round 2's
`test_timestamp_response_asks_the_module_clock_not_a_fixed_string` because its stub discarded
`fmt`. Closed by capturing `fmt` into a list and asserting its exact value.

---

## 19. A two-valued knob mounted through different mechanisms per value, so only one direction exercises the real parse

**Assertion form:** a boolean config knob has one guard per state — an OFF guard and an ON
guard — which looks like full coverage of both directions (the mirror of shape 3, which is
about a direction never mounted at all). But the two guards reach the value through different
mechanisms: the OFF guard reloads the real module against a real (cleared) env var, so it runs
the actual `os.environ.get(...)` parse; every ON guard instead does
`monkeypatch.setattr(config, "KNOB", True)` on the already-imported module, which never touches
`os.environ` or the parse expression at all.

**Mutation that defeats it:** dead-code the parse so it can never produce `True`, while leaving
the string default intact — `TERMINAL_TIMESTAMPS = os.environ.get(...)` → `TERMINAL_TIMESTAMPS =
False and os.environ.get(...)`. The OFF guard still passes (the expression still evaluates to
`False` with no env var set — dead-coding the true-producing half doesn't touch the false
default). Every ON guard still passes too, because none of them evaluate that expression at
all — they overwrite the attribute directly, downstream of the parse entirely. The knob is now
permanently OFF regardless of the env var, and nothing goes red.

**Why this is distinct from shape 3:** shape 3 is a direction that is never mounted at all — no
assertion ever reads the OFF state. Here, both directions ARE asserted; the gap is that the two
assertions don't exercise the same code. One pins the parse, the other pins something
downstream of it, and the mutation lives in the part only the first one reaches — so from a
glance at "is there an OFF test and an ON test," coverage looks symmetric when it isn't.

**Guard form that survives:** mount the ON direction the same way as the OFF direction — set
the real env var (`monkeypatch.setenv("CHELA_TERMINAL_TIMESTAMPS", "true")`) and reload the
real module, then assert the reloaded attribute is `True`. This runs the actual parse
expression in both directions, so a dead-coded half of it is caught regardless of which half.

**Found:** CMX-277 rework round 4 (2026-08-14), PR #348 — the judge's `TERMINAL_TIMESTAMPS =
os.environ.get(...)` → `TERMINAL_TIMESTAMPS = False and os.environ.get(...)` mutation survived
because every ON-state test in `tests/test_hooks.py` monkeypatched the `TERMINAL_TIMESTAMPS`
attribute directly, and the one env-reload test in the file (`test_terminal_timestamps_defaults_off_with_no_env_var_set`)
only mounted the OFF direction. Closed by adding
`test_terminal_timestamps_turns_on_with_the_env_var_set_to_true`, which reloads the real module
against a real `CHELA_TERMINAL_TIMESTAMPS=true` env var.

---

## 20. A short-circuit's membership set is proven, but never proven together with the state that would make widening it dangerous

**Assertion form:** a dispatcher-style function has an early `if event in SOME_SET and FLAG:
return X` that is meant to intercept only a couple of named events and fall through to
everything else unchanged. One test pins the set's exact membership (`SOME_SET ==
frozenset({...})`); other tests drive each member event through the branch with `FLAG` on;
still other tests drive the events the branch is protecting (a *different* event, further
down the function) with `FLAG` at its real default. No test ever combines "an event the later
branch cares about" with "`FLAG` on" — because every fixture that turns `FLAG` on also happens
to only POST the early-branch's own events, and every fixture that POSTs the later branch's
event happens to run at `FLAG`'s real (off) default.

**Mutation that defeats it:** widen the membership check with an `or event == "<later branch's
event>"` clause. The early branch now also intercepts and returns for that event whenever
`FLAG` is on — silently skipping whatever the later branch does (a side effect, not just a
different return value) with `FLAG` in the one state no fixture ever paired with that event.
The membership-equality test still passes (it never says the check is *only* membership,
just what the set contains); every early-branch test still passes (none of them touch the
later branch's event); every later-branch test still passes (none of them turn `FLAG` on).

**Why this is distinct from shape 12:** shape 12 is a loop that should stop at the first match
but is tricked into falling through to consult more entries. Here there is no loop — it's a
single boolean short-circuit whose *members* are proven correct in isolation, but never
proven not to swallow a sibling branch once independently-true guard conditions (set
membership, and a config flag) are combined. The gap is combinatorial coverage of two
independently-toggled conditions, not fall-through.

**Guard form that survives:** drive the later branch's event through the endpoint with the
early branch's flag deliberately turned ON, and assert two things at once — the response body
still has the *un-intercepted* shape (proving the early branch did not return early for this
event), and the later branch's own side effect still fired (proving control actually reached
it, not just that the return value looked right by coincidence).

**Found:** CMX-277 rework round 5 (2026-08-14), PR #348 — the judge's `if event in
hooks.TIMESTAMP_EVENTS and config.TERMINAL_TIMESTAMPS:` → `if (event in
hooks.TIMESTAMP_EVENTS or event == "PostToolUse") and config.TERMINAL_TIMESTAMPS:` mutation
in `chela/dashboard/app.py` survived because the flip to `TERMINAL_TIMESTAMPS` defaulting OFF
(round 2) meant every ON-state test only POSTed `UserPromptSubmit`/`Stop`, and every
`PostToolUse` test ran at the real (OFF) default — so no fixture ever POSTed `PostToolUse`
with timestamps ON, which is exactly the combination the mutation needs to steal
`gateanswer.gate_resolved()` and reproduce the CMX-54 regression (a held gate waiting out its
whole budget). Closed by
`test_timestamps_on_does_not_steal_the_post_tool_use_gate_resolution`, which sets
`TERMINAL_TIMESTAMPS = True`, POSTs `PostToolUse`, and asserts both the body is `{}` and
`gate_resolved` was still called.

---

## 21. A shared field value tested on the sibling that motivated it, not on the one that already had it

**Assertion form:** two (or more) declarations set the same field to the same value —
`live_reload=True` on both `worktree_disk_budget` and `memory_slice_budget`. A PR adds the
field and a test that exercises what the field actually *does* (here, `capabilities.live()`
reconciling a `live_reload` capability against fresh config instead of a stale boot snapshot)
— but only for the capability the PR's own ticket was about. The sibling declaration, present
before the PR and unchanged by it, reads as "already covered" because a plain `effective()`
test already asserts its `on`/`detail` values — except `effective()` always reads config
fresh regardless of the flag, so that test cannot distinguish `live_reload=True` from
`live_reload=False`. Nothing in the suite ever drives the sibling through `capabilities.live()`.

**Mutation that defeats it:** flip the sibling's `live_reload` to the opposite of what its own
comment says it must be. Every existing test for that capability — the `effective()` tests
and the dispatcher-gate tests — reads config directly and never goes through `live()`, so none
of them notice.

**Guard form that survives:** when a field is declared identically on N siblings and a new
test proves what the field does for sibling K, ask whether siblings 1..N-1 already have an
equivalent test — not just a test with the same *name shape* (`test_capability_reports_*`),
but one that actually exercises the code path the field controls. If they don't, they are
untested for that field, no matter how long they've been in the suite.

**Found:** CMX-280 rework round 1 (2026-08-14), PR #351. `_memory_slice_capability` was new
in this PR and got `test_memory_slice_budget_reflects_a_post_boot_env_change_not_the_boot_snapshot`
driving it through `capabilities.live()`. `_worktree_disk_budget_capability` had carried
`live_reload=True` since CMX-164, predating this PR, with only `effective()`-level tests
(`test_capability_reports_off_by_default`, `test_capability_reports_on_with_the_human_size`)
— both blind to the flag. The judge flipped `worktree_disk_budget`'s `live_reload` to `False`
in a throwaway checkout; 3083 tests, including every `worktree_disk_budget` test, stayed
green. Closed by adding
`test_worktree_disk_budget_reflects_a_post_boot_env_change_not_the_boot_snapshot`, mirroring
the memory-slice test: publish a boot snapshot with the budget off, then set the env var with
no restart and assert `capabilities.live_capability("worktree_disk_budget")` picks it up.

---

## 22. A field declared identically on every branch of one function, tested through only one branch

**Assertion form:** a single function returns several `Capability` objects — one per
branch (knob-on-and-usable, knob-on-but-unusable, externally-bound, off) — and every
branch declares the same field (`live_reload=True`). One test drives a boot snapshot
through the code that actually reads the field (`capabilities.live()`) — but only by
publishing from ONE of the branches (usually whichever one the fixture defaults to) and
flipping config so the *next* read lands on a different branch. That proves the flag
matters on the branch that was published from; it proves nothing about the flag on the
other N-1 branches, because `live()` only ever consults the flag on the row that was
actually in the boot snapshot.

**Mutation that defeats it:** flip `live_reload` to `False` on any branch OTHER than the
one the existing test happens to publish from. Every `effective()`-level test for that
capability reads config fresh and can't tell the flag apart; the one `live()` test never
publishes a snapshot FROM the mutated branch, so it never exercises that branch's own
flag either.

**Guard form that survives:** for each branch of the function, publish a boot snapshot
from THAT branch specifically, then change config so a later read would land on a
different branch, and assert `live()` picks up the change. One test per branch, not one
test for the function.

**Found:** CMX-280 rework round 3 (2026-08-14), PR #351 — this is
[[21|entry 21]] recurring one level down: not siblings across two functions, but branches
inside one. `_memory_slice_capability` returns four `Capability` objects (memcap-available
ON at `capabilities.py:161`, set-but-unwrapped OFF at `:168`, external-bound ON at `:186`,
plain OFF at `:192`), all `live_reload=True`. Round 2 added
`test_memory_slice_budget_reflects_a_post_boot_env_change_not_the_boot_snapshot`, which
publishes from the `:192` OFF branch and flips to the `:161` ON branch — proving `:192`'s
flag matters, saying nothing about `:161`'s. The judge flipped `:161`'s flag to `False` in
a throwaway checkout; 3095 tests, including that test, stayed green. Closed by adding
`test_memory_slice_budget_on_going_off_live_does_not_stay_latched` (publishes from `:161`,
flips live to off) and its two siblings for `:168` and `:186`.

---

## 23. Two rendered quantities collide on the same substring, so mutating one is invisible

**Assertion form:** a detail string renders more than one distinct numeric quantity
derived from the same inputs (a ceiling, and a headroom computed as ceiling-minus-current)
side by side in prose. The guard asserts each *expected* number appears as a substring —
but only pins the ceiling's value, not the headroom's, and the two would print identically
if headroom were computed wrong (e.g. mistakenly re-emitting the ceiling instead of
subtracting).

**Mutation that defeats it:** change the derived quantity's formula so it collapses onto
the OTHER quantity's value (`headroom = human_size(max_bytes)` instead of
`human_size(max_bytes - current)`). The string still contains every substring the guard
checks — the ceiling's value was already being asserted, and now headroom prints the same
digits — so the suite stays green even though the number that matters (how much room is
actually left) is wrong.

**Guard form that survives:** when a string renders two or more numbers derived from the
same inputs, choose fixture values where every rendered number is numerically distinct,
and assert each one by the surrounding words that make it unambiguous which quantity it
is (`"currently using 10.0G"`, `"~2.0G headroom"`) — not just the bare digits, which could
belong to either.

**Found:** CMX-280 rework round 3 (2026-08-14), PR #351 —
`test_capability_reports_an_external_bound_as_on` asserted `"12.0G"` (the ceiling) and
`"83%"` but never pinned the headroom value by itself; with `max_bytes=12G` and
`current=10G`, correct headroom is `"2.0G"`, and the judge's mutation
(`headroom = config.human_size(bound["max_bytes"])`) made headroom render `"12.0G"` too —
a substring the test already asserted for the ceiling. 3095 tests stayed green. Closed by
asserting `"currently using 10.0G"` and `"~2.0G headroom"` as distinct, unambiguous
substrings.

---

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

---

## 26. A large green suite as false comfort for a claim that has zero guard of its own

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

## 27. Coverage deleted alongside the feature it shared a *file* with

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

## 28. A guard closed to the exact width of the blocking finding, leaving a named remainder undefended

**Assertion form:** a judge round's blocking finding names a gap and prescribes a fix that
covers MORE ground than the finding strictly requires — e.g. a non-blocking note beside the
finding says "one fixture covering A, B and C would close this" — and the rework closes only
the narrowest slice that makes the blocking finding itself go away (A), leaving B and C
exactly where the note found them. The suite goes green, the round passes, and — because
non-blocking notes cost no round and block nothing — the fact that B and C are still
unguarded carries **no signal** into the next round. It reads as closed until a future judge
round independently re-derives B or C from scratch.

**Mutation that defeats it:** corrupt B or C. Nothing added by the "fix" reaches either one,
so the corruption ships clean — while the PR now claims (via the closed finding) that the
whole area is guarded.

**Guard form that survives:** when a judge note prescribes a fix wider than the blocking
finding strictly requires, close the WHOLE prescription in the same round, not just the part
that makes the round pass — the marginal cost of the rest is usually small (it is often one
extra fixture row, not a new file) and a note that named the gap and was only partially acted
on is exactly the shape the next round is built to find.

**Found:** CMX-279 rework round 3 (2026-08-14), PR #350. Round 2's non-blocking note named
three unguarded `knInline`/`knLink` rules — bold spans, links, and the two `knInline(
displayTitle(...))` call sites in kanban.js/taskmodal.js — and prescribed "one fixture ...
covering a bullet run, a bold span and an .md link would close all three at once." The round-2
rework took only the bullet run (closing DEFEAT_SHAPES #18, the blocking finding) and left
bold/links/call-sites exactly where the note found them. Round 3's judge re-derived all three
as blocking mutations. Closed by extending `tests/taskmodal_model.test.mjs`'s knMd fixture to
cover a bold span, an external link, an in-bundle `.md` link and a `#anchor` link in one
assertion, plus two independent DOM-level wiring tests (`tests/kanban_flatten.test.mjs` and
the new `tests/taskmodal_render.test.mjs`) driving each `knInline(displayTitle(...))` call
site through its real caller.

**Recurred:** CMX-279 rework round 4 (2026-08-14), same PR #350, same underlying note — it had
named FOUR gaps (blockquote, fenced code, plus the two already covered above), and round 3
only closed the two it was blocking on. The blockquote and fenced-code branches were still
byte-identical to where round 2 found them; round 4's judge re-derived both as blocking
mutations a second time, plus two more the note never explicitly named (knInline's own
`escHtml` call, and `attrEsc` on knLink's href — both real behaviour the PR's rewritten code
carries, just never exercised by a fixture with an HTML-special character or a quoted href).
Closed by three more assertions in the same `tests/taskmodal_model.test.mjs` (blockquote+fence
in one fixture, escHtml, attrEsc-on-quote) plus the third `knMd` call site as a fourth
DEFEAT_SHAPES #7 wiring test (see above). The standing lesson: when a note names N gaps and a
blocking finding only forces closing a subset, close ALL N in the same round — a partial close
does not make the round's own note stop being a to-do list for the next judge.

## 29. An exact-output fixture whose payload is IDENTITY under the very transform it claims to guard

**Assertion form:** an exact-output test asserts a string produced by a transform function
(an escaping call, a level-pinning regex capture, a character-class alternative) — and the
fixture's *value* happens to be a fixed point of that transform: running the transform or
skipping it entirely produces the same output. The test's own doc comment may even name the
transform as the thing it guards, and the guard is not lying — it genuinely calls the
function it says it does. It just never gives that function anything to do.

**Mutation that defeats it:** delete or narrow the call (skip the escaping, pin the captured
level to whatever constant the fixture always uses, narrow a character class to the one
alternative the fixture always hits). The fixture's output is unchanged, because the
transform was a no-op on that particular input — the assertion cannot tell "the transform ran
and did nothing" apart from "the transform did not run."

**Guard form that survives:** for any assertion meant to pin a transform, pick a payload for
which the transform PROVABLY changes the output — a string containing the characters an
escaper actually escapes, a value other than whatever every other fixture in the file already
uses, a case exercising every alternative in a character class rather than just one. State
*why* the payload is diagnostic (which property of the input makes the row non-identity) so a
reviewer widening the suite later can check the claim against the code instead of re-deriving
it from scratch.

**Found:** CMX-279 rework round 5 (2026-08-14), PR #350. Three of `knMd`'s exact-output guards
were each built from a fixture that happened to be a fixed point of the branch it was meant to
pin: the round-4 fenced-code fixture (`const x = 1;`, `**not bold**`) has nothing for
`escHtml` to escape, so dropping the `escHtml` call inside the fence left the assertion
byte-identical; every heading fixture in the whole suite used `###`, so pinning the heading
level to the constant `3` (instead of reading `h[1].length`) passed; and the list-item regex
fixture only ever used `-` bullets, so narrowing `/^[-*]\s+(.*)$/` to `/^[-]\s+(.*)$/` passed
too. All three shipped clean through `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3076 passed,
0 failed). Closed by a single table-driven test (`KN_MD_BRANCH_TABLE` in
`tests/taskmodal_model.test.mjs`) enumerating every branch of `knMd` from the source, with each
row deliberately picked to be non-identity under whatever it guards — an HTML-special-character
payload inside the fence, one row per heading level 1 through 4, and both list-marker
characters — plus two negative-control rows (an unterminated fence, and an ol→ul list-kind
switch) for branches the round-5 finding did not name, to prove the table closes the space
rather than answering only the four findings asked for.

---

## 30. A branch-enumeration table proves the dispatch fired, not that the transforms nested inside it ran

**Assertion form:** a table enumerates every branch of a dispatcher function, one row per
entry condition (does this `if` fire, does it emit the right tag) — the correct response to
shape #24, and each row's *own* payload is deliberately non-identity for the thing that row
was written to pin. But a branch often does more than open a tag: it also calls a shared
helper partway through its body (an inline-rendering/escaping function, a state-closing
function like "close whatever was open before this new thing starts"). The table's per-row
payload was chosen to make the *branch's own* transform non-identity and nothing checked
whether it was ALSO non-identity for every helper nested inside that branch, or whether the
state the helper is supposed to clean up was actually dirty when that row's fixture ran.

**Mutation that defeats it:** drop or dead-code the nested helper call inside a branch whose
row payload happens to be a fixed point of that helper (plain alphanumeric text is identity
under an inline-markdown/escaping call), or whose row payload never puts the branch in the
state the helper exists to clean up (no list open when the row's fixture reaches that
branch). The row's own assertion — pinned to the branch's own tag — is unaffected, because
the corruption is one level below what that row was checking.

**Guard form that survives:** treat the table as enumerating a cross-product, not a single
axis — for every branch that calls a shared inline-rendering helper, include a row whose
payload is non-identity under THAT helper too (not just under the branch's own dispatch); for
every call site of a shared state-closing helper, include a row that actually has the state
open immediately before that branch fires, with no intervening blank line or other
state-clearing branch. Count the helper's call sites from the source (not from what the
current fixtures happen to reach) and check each one off explicitly.

**Found:** CMX-279 rework round 6 (2026-08-14), PR #350, recurring one level below shape #24
inside the very table shape #24 was closed by. The round-5 `KN_MD_BRANCH_TABLE` gave its
blockquote row (`> quoted`) and its four heading rows (`# h1` … `#### h4`) plain-text
payloads — non-identity for the branch's own dispatch (does it emit `<blockquote>`/`<hN>`),
but identity under `knInline`, the helper both branches call to render their content. Dropping
`knInline` from either branch left both rows byte-identical, even though the same table had
already fixed this precise defect one branch over, for the fenced-code row. Separately,
`closeList()` — the helper that closes a still-open `<ul>`/`<ol>` before a new block starts —
has eight call sites in `knMd`; the round-5 table's rows exercised six (heading, blank line,
both list-kind switches, the fence, and the EOF tail) but no row put a list open immediately
before a blockquote or a plain paragraph line, so dead-coding either of those two call sites
left every existing row's assertion unchanged. All four survived
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3080 passed, 0 failed) in the judge's own
mutation checkout. Closed by six more rows in the same `KN_MD_BRANCH_TABLE`: a blockquote and
a heading row each carrying a bold span *and* HTML-special characters (non-identity under
`knInline`), and a blockquote and a plain paragraph line each placed directly after an open
`-` run with no blank line between (state dirty when the branch fires, so a dead-coded
`closeList()` nests the new block inside the list's last `<li>` instead of closing it first).

---

## 31. A shared boolean flag is proven on ONE rendering branch and assumed for a sibling branch of the same function

**Assertion form:** one render function computes a single flag once (`const isEnv =
k.source === 'env'`) and then consumes it at two independent template branches further
down — a `kind === 'number'/'text'` branch producing an `<input>`, and a `kind === 'bool'`
branch producing a `<select>`. A test suite proves the flag disables the `<input>` branch
(the only branch its fixtures happen to use, because the one fixture with `source: 'env'`
never also sets `kind: 'bool'`), and a reviewer scanning "is env-precedence tested?" sees a
passing disabled-field assertion and reasonably calls the whole function covered.

**Mutation that defeats it:** leave the shared `const isEnv = k.source === 'env'` computation
untouched (mutating it would fail the `<input>`-branch test, so this is a *narrower*,
harder-to-notice cut) and instead hardcode the disabled-ness at only the `<select>` branch's
own template site — `${isEnv ? 'disabled' : ''}` -> `${false ? 'disabled' : ''}` inside the
`kind === 'bool'` block specifically. Every existing test (which only ever drives `source:
'env'` through non-bool knobs) stays green; a bool-kind knob whose env var is set now renders
an editable `<select>` that silently discards whatever the operator picks.

**Why this is distinct from shape 22:** shape 22 is one function returning several sibling
*objects*, all declaring the same field, with `live()` only reading the field off whichever
object a boot snapshot happened to publish. Here there is one object (one knob) and one flag,
but the flag is *consumed* at two different template branches inside the same render call —
the gap is which branch's own copy of the ternary a test's fixture happens to reach, not
which object a snapshot happens to publish from. The fix pattern (drive every branch
independently) is the same lesson recurring at the template-consumption layer instead of the
object-construction layer.

**Guard form that survives:** when a shared flag feeds N template branches inside one render
function, construct a fixture that reaches EACH branch with the flag in its "guarded" state —
not just the branch every other test in the file already happens to use. `git grep` the flag
inside the function body to count how many places consume it, the same census shape 7
prescribes for call sites.

**Found:** CMX-287 rework round 1 (2026-08-14), PR #358. The verdict's own reported mutation
(`k.source === 'env'` -> `false`) does NOT survive against this repo's actual test suite —
`settings_timing.test.mjs` and `settings_dispatch.test.mjs` predate CMX-287 unchanged and
already drive that exact mutation red (verified by hand: 3/7 tests in those two files fail
under it). What WAS genuinely unguarded is one level narrower: `settings_dispatch.test.mjs`'s
only `source: 'env'` fixture (`merge_base`) is a plain text knob, so it only ever reaches
`_renderDispatchRows`'s `<input>` branch. A `kind: 'bool'` knob with `source: 'env'`
(`judge_enabled` in the fixture below) reaches a separate `<select>` branch whose own
`${isEnv ? 'disabled' : ''}` was never independently driven — hardcoding it to `${false ?
'disabled' : ''}` left every pre-existing test green. Closed by
`tests/settings_modal_precedence.test.mjs`, which also re-drives the reviewer's original
mutation through the real `#settings-tabs`/`.settings-tabpanels` modal markup (the pre-CMX-287
fixtures use a bare `#settings-drawer`/`#drawer-body` div) rather than through the older
drawer's minimal DOM, so no future round can pin the "decorative" claim on the DOM shape
differing from what a user actually sees.
