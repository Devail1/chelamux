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

## 9. Substring assertions on a nested payload never pin its wrapping envelope

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

## 10. A "stop, don't fall through" rule untested because every fixture has the property everywhere

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

## 11. A per-item loop over a required SET tested with a set of exactly one

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

## 12. A field pinned at one hop of a round-trip, untested at the next

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

## 13. A list rendered verbatim, tested with a list of exactly one — the render-side mirror of shape 11

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
the first, the agent copies the JSON exactly as instructed, and shape 11's own fix —
correctly — flags the second as missing. The agent is then refused for omitting a mutation
it was never shown, with no way to discover what it is: an unescapable refuse-loop produced
by fixing one side of a pair and not the other.

**Guard form that survives:** construct a fixture with at least TWO items in the required
set and assert that BOTH appear in the rendered output by their distinguishing fields — not
just that "a" required-mutation section exists, and not just fields the first item alone
would already satisfy.

**Found:** `chela/dispatcher.py`'s `_required_mutations_section`, at both call sites that
render it (CMX-269 rework round 7) — the same two tests fixed for shape 12
(`test_the_rework_prompt_carries_the_REQUIRED_MUTATION_SET_as_a_copy_pasteable_JSON_block`,
`test_a_re_nudged_rework_ALSO_carries_the_REQUIRED_MUTATION_SET`) still built their
`mutations` list with exactly one entry, so round 6's enforcement-side fix for shape 11 had
no render-side counterpart. Fixed by giving each fixture a second, distinct mutation and
asserting both survivors' `guard` and `file` values appear in the rendered prompt.
