## 318. A negative control that cites its sibling fixture drifts the one axis that made the sibling work

**Assertion form:** a new negative control's own docstring says it mirrors an existing,
already-proven guard for a structurally identical hazard — `final_message`'s CMX-191 aliasing
risk is the same shape as `did_work_since`'s, and the new test says so, in nearly the same
words. But the fixture it actually builds changes the one detail that made the original
guard a real negative control: `did_work_since`'s guard puts both windows in **one** shared
cwd (one project directory, two transcripts) so a directory-keyed lookup provably hands one
window the other's evidence. The new test puts each window in its **own**, distinct cwd —
two project directories, one transcript apiece — and says so in its own docstring ("two
DISTINCT (unshared) working directories"). A lookup keyed on "whichever transcript sits in
this window's project directory" resolves each of those correctly, by construction, because
there is only ever one file per directory to find.

**Mutation that defeats it:** replace the trusted, already-resolved path with a directory
scan that returns *some* file from the same parent directory: `return
transcripts.last_assistant_text(path)` becomes `siblings = sorted(path.parent.glob("*.jsonl"));
return transcripts.last_assistant_text(siblings[-1] if siblings else path)`. Under the
two-distinct-cwds fixture this changes nothing observable — each `path.parent` holds exactly
one `*.jsonl`, so `siblings[-1] is path` always. Under a one-shared-cwd fixture (two
transcripts filed via each window's own resolved session id, both living under the one project
directory Claude Code actually writes to for that cwd) the same mutation picks whichever
filename sorts last, independent of which window asked — exactly the CMX-191 aliasing the test
exists to catch.

**Why citing the sibling doesn't transfer the property:** the docstring reads as evidence that
the coverage gap from [[311|shape 311]] ("never mirrored at all") was closed, because it names
the sibling test and claims to reproduce its hazard. But mirroring a *test name and rationale*
is not mirroring a *fixture* — the one axis that made `did_work_since`'s guard bite (one
directory, several files, ambiguous which belongs to whom) is precisely what got substituted
away in the retelling, and nothing about invoking the sibling's name checks that the new
fixture still triggers the same failure mode. A reviewer who sees "mirrors
`test_did_work_since_refuses_a_shared_cwd_rather_than_crediting_a_sibling`" in the docstring has
every reason to assume the shared-cwd shape survived the port; it did not.

**Guard form that survives:** when a new negative control's docstring claims to mirror an
existing one for "the same hazard," diff the two fixtures' *setup*, not their prose — same
number of distinct directories, same number of files per directory, same resolution tier
exercised. If the original hazard specifically requires N≥2 files sharing one lookup key and
the new fixture gives each window its own key, the mirror is incomplete regardless of how
closely the docstrings read. For this shape specifically: put the sibling transcripts under
one project directory (matching what Claude Code really writes for two agents sharing a cwd),
resolved via each pane's own session id rather than the cwd-guess tier, so a directory-keyed
shortcut has more than one file to choose wrong from.

**Found:** `chela/inbox.py`'s `final_message` (CMX-318 rework round 2, PR #396).
`test_final_message_refuses_to_quote_a_sibling_rather_than_this_window` cited
`test_did_work_since_refuses_a_shared_cwd_rather_than_crediting_a_sibling` as its model but used
`/home/x/proj7` and `/home/x/proj8` — two project directories, one file each — so a
directory-glob mutation on `final_message` resolved both windows correctly and
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3343 passed) with the mutation
applied. Closed by moving both transcripts under one shared project directory, resolved via
each pane's own `--resume <sid>` (the "cmdline" tier) rather than the cwd-guess tier the real
resolver refuses on for a shared origin — the same mutation now returns the wrong window's
words and the test goes red.

**See also:** [[311|shape 311]] — the antecedent gap (no mirror at all); this shape is what can
still slip through even after the mirror is written, if the fixture drifts the load-bearing
axis during the port.

---

### Round 3, same task: a comment's load-bearing detail is never exercised at the point it diverges

A second, distinct shape surfaced on the same CMX-318 branch one round later — reusing this
file's number rather than opening a second one, per docs/DEFEAT_SHAPES.md's "reuse that number
instead of computing a new one."

**Assertion form:** a comment or docstring explains WHY a specific implementation choice was
made instead of an equally plausible-looking alternative — curly quotes instead of `"` because
straight quotes are a shell metacharacter the downstream sanitizer strips to a space; a hard
`[:N]` slice instead of the raw string because the value is unbounded, agent-authored text about
to be persisted. Every test that exercises the surrounding feature asserts the *substance*
survives (the excerpt text appears in the notice; the message appears in the payload) but none
of them is built to fail if the specific mechanism the comment argues for were swapped for the
alternative it explicitly warns against — because no fixture ever reaches the place the two
choices produce different output.

**Mutation that defeats it:**
- Swap the argued-for character for its plain-ASCII look-alike: `f" Said: “{...}”"` →
  `f' Said: "{...}"'`. Every existing test's `said` fixture (`"Fixed the parser and added 3
  tests, all green"`, `"line one\nline two"`, `"w" * 5000`, a 166-char sentence) only ever
  asserts that the excerpt's *text* is present or absent — never that a quote character brackets
  it — so a straight-quoted frame passes every assertion identically to a curly-quoted one, even
  though `sanitize_prompt`'s `SHELL_META_RE` (which every one of those same tests routes through)
  strips `"` to a space and the delimiter silently disappears from the real pushed line.
- Drop the cap: `payload["final_message"] = said[:FINAL_MESSAGE_PAYLOAD_CHARS]` →
  `payload["final_message"] = said`. The one existing payload test's fixture (`"detail " * 40`,
  280 chars) sits comfortably under `FINAL_MESSAGE_PAYLOAD_CHARS` (4000), so slicing at 4000 and
  not slicing at all produce byte-identical output for that fixture — the assertion
  `payload["final_message"] == long_text` passes either way, and the cap is provably
  unexercised.

**Why this is distinct from the shape above:** the shape above is a negative control's *fixture
shape* drifting from the sibling it claims to mirror — the coverage gap is nameable by comparing
two tests' setups. Here nothing is missing from the fixture list in an obviously nameable way —
the feature has real, passing coverage of its headline behavior — the gap is that a comment names
a *reason* for a specific choice, and that reason is a claim about behavior at a boundary (a
sanitizer pass, a length cap) that no fixture happens to sit on.

**Guard form that survives:** when a comment explains "we chose X over Y because Z", write the
test that specifically exercises Z, not just a test that X's overall output looks right:
- For a delimiter/framing choice defended against a specific downstream transform, assert the
  delimiter characters themselves survive that transform in the final output (`f"“{said}”" in
  text`), not just that `said`'s words appear somewhere in it.
- For a numeric cap defended as "keeps a persisted value bounded", pick a fixture that is
  provably *longer* than the cap and assert the stored value equals the input sliced at the cap —
  a fixture sized anywhere at or under the cap cannot distinguish "capped" from "uncapped" no
  matter how carefully its assertion is worded.

**Found:** `chela/inbox.py`'s `_line`/`final_message` payload write (CMX-318 rework round 3, PR
#396). `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3344 passed) under both
mutations above, applied independently, in a throwaway checkout of the PR head. Closed by
`test_the_said_excerpt_stays_curly_quoted_through_sanitization` (asserts `f"“{said}”" in text` on
the real pushed line, so a straight-quoted frame goes red the moment `SHELL_META_RE` eats its
delimiters) and `test_the_payload_final_message_is_capped_at_the_payload_limit` (a 7000-char
fixture, asserting the stored payload equals the input sliced at `FINAL_MESSAGE_PAYLOAD_CHARS`,
so dropping the slice goes red).

---

### Round 4, same task: every test at a seam stubs BOTH sides of it to constants, so neither side's own value is ever checked

A third, distinct shape surfaced on the same CMX-318 branch one round later — reusing this
file's number again, per docs/DEFEAT_SHAPES.md's "reuse that number instead of computing a
new one."

**Assertion form:** a function `g(x)` is called from a call site `f()` that computes `x` itself
(here, `agent_events` computes `wid` — the window it is currently processing — and passes it to
`final_message(wid)`). Every test exercising `f()` stubs `g`'s OWN internal dependencies
(`sessions.transcript_for_window`, `transcripts.last_assistant_text`) to constants that ignore
whatever argument they are called with — `lambda wid: Path(f"/proj/{wid}/session.jsonl")` looks
parametric but every window in the fixture produces the same downstream text regardless, and
`lambda path: said` ignores its argument outright. So the tests exercise "does `f()`'s output
reflect *some* call to `g`" but never "did `f()` pass `g` **its own** `x`, as opposed to a
different value entirely." A call site that hardcodes `g`'s argument — `final_message(wid)` →
`final_message("@1")`, i.e. always the orchestrator's own window instead of whichever window
`agent_events` is actually reporting on — produces identical output under every existing
fixture, because none of them ever puts a DIFFERENT expected value behind a different wid.

**Mutation that defeats it:** `said = final_message(wid)` → `said = final_message("@1")` at the
`agent_events` call site. `ORCH = "@1"` in the test fixtures, so this silently substitutes the
orchestrator's own window for whichever agent window actually finished — the exact CMX-191
misattribution `final_message`'s own docstring warns about, now reintroduced one call frame
outside the function that was hardened against it directly.

**Why testing `final_message` alone doesn't close this:**
[[318|shape 318 round 2]] (the sibling-fixture drift, closed by
`test_final_message_refuses_to_quote_a_sibling_rather_than_this_window`) proves `final_message`
resolves correctly **when called directly with a given wid**. It says nothing about whether the
*caller* passes the right wid in the first place — that is a property of `agent_events`, not of
`final_message`, and no amount of hardening inside `final_message` can catch a mutation at its
call site.

**Guard form that survives:** for a call site that computes its own argument from context, stub
the CALLEE itself as a spy that records what it was called with, and assert the argument
against the context the test set up — independent of what the callee's own internals would do
with it. `test_the_finished_notice_resolves_final_message_against_this_windows_own_wid`
monkeypatches `inbox.final_message` (not its dependencies) to a function that records every
`wid` it is called with and returns a value keyed off that `wid` (`f"words from {wid}"`), then
asserts both that the recorded wid is exactly the watched AGENT window and that the notice
carries the AGENT-keyed value — so a hardcoded `"@1"` call site is caught two ways: the wrong
wid is recorded, and the wrong (ORCH-keyed) text would appear in the notice.

The companion gap closed in the same round: `final_message`'s docstring promises "an
unresolvable window … yields None," but `test_the_notice_falls_back_to_the_template_when_the_
agent_said_nothing` only ever left the transcript ITSELF empty (`last_assistant_text` stubbed
to `None`) while `transcript_for_window` kept returning a path — so `if path is None: return
None` could be swapped for `if path is None: return "finished the task"` with nothing going
red. `test_final_message_returns_none_when_the_window_itself_is_unresolvable` stubs
`transcript_for_window` alone to return `None` and asserts `final_message(...)` is `None` — the
arm the tool-only test structurally cannot reach.

**Found:** `chela/inbox.py`'s `agent_events` call site and `final_message`'s unresolvable-window
arm (CMX-318 rework round 4, PR #396). `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
(3346 passed) under both mutations above, applied independently, in a throwaway checkout of the
PR head. Closed by `test_the_finished_notice_resolves_final_message_against_this_windows_own_wid`
and `test_final_message_returns_none_when_the_window_itself_is_unresolvable`.

---

### Round 5, same task: every fixture satisfies an OR of two completion conditions jointly, so a call site that silently narrows the OR to one arm is invisible; and a guarded write's own test never checks the guard's negative side

A fourth and fifth, distinct shape surfaced on the same CMX-318 branch two rounds later —
reusing this file's number again, per docs/DEFEAT_SHAPES.md's "reuse that number instead of
computing a new one."

**Assertion form (the OR-narrowing half):** `agent_events` calls a completion "finished" when
`finished_edge OR finished_evidence` — two independently-computed booleans that mean different
things (`finished_edge` is the ordinary busy→idle transition; `finished_evidence` is the
fallback for a transition the poller missed, proven instead via `did_work_since`). Every test
that drives a "finished" event builds a fixture that happens to satisfy BOTH at once:
`_finished_with_transcript` registers a fresh watch (so `since` predates the stub) and stubs
`last_assistant_activity_at` to a timestamp after it, making `finished_evidence` true, while the
watch's own idle-confirm bookkeeping (with `IDLE_CONFIRM_SECONDS` collapsed to 0) makes
`finished_edge` true too on the very same tick. No fixture ever isolates one arm from the other,
so nothing distinguishes "the excerpt is wired to `finished_edge OR finished_evidence`, as the
code reads" from "the excerpt is wired to `finished_evidence` alone" — a call site that quietly
re-gates on just the fallback arm produces byte-identical output under every existing fixture.

**Assertion form (the guarded-write half, unrelated shape found in the same round):**
`payload["final_message"]` is written only `if said:` — the guard exists specifically so an
agent that said nothing (a tool-only final turn, an unreadable transcript, an unresolvable
window) does not get a fabricated value persisted. The one test covering the said-nothing path
(`test_the_notice_falls_back_to_the_template_when_the_agent_said_nothing`) asserts only that the
pushed *summary* omits "Said: …" — it never inspects the *payload* at all, so nothing proves the
`final_message` key is ABSENT from the persisted record on that path. A test can prove a guarded
write produces the right value when the guard is true without ever proving the write is skipped
entirely when the guard is false.

**Mutation that defeats it:**
- OR-narrowing: `said = final_message(wid)` → `said = final_message(wid) if finished_evidence
  else None` at the `agent_events` call site. An agent whose completion is caught by the
  ordinary busy→idle edge (`finished_edge`) alone — the common case — now gets the pre-CMX-318
  template with no excerpt, and nothing sees it: `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
  stayed green (3348 passed) with this applied.
- Guarded-write: `if said: payload["final_message"] = said[:FINAL_MESSAGE_PAYLOAD_CHARS]` →
  `if True: payload["final_message"] = (said or "")[:FINAL_MESSAGE_PAYLOAD_CHARS]`. Every
  said-nothing completion now persists `final_message: ""` — the exact value
  `last_assistant_text`'s own docstring calls out as indistinguishable from a bug ("None means
  'no text found', never ''"). Also stayed green (3348 passed) with this applied, independently.

**Why this is distinct from the shapes above:** [[318|shape 318 round 4]] is about a callee's
argument never being independently checked against context; this OR-narrowing gap is about
*which of two disjunctive conditions* a piece of code downstream of both actually depends on —
the conditions themselves are computed correctly and independently, but no fixture ever makes
them disagree, so a consumer that silently drops from "either" to "only one" is unreachable by
any assertion. The guarded-write gap is a different, ordinary-looking absence: nearly every test
in this file (and the wider suite) proves a conditional write's *value* when the condition holds;
almost none prove the write is *skipped* when it doesn't, because "nothing happened" leaves
nothing obvious to assert on unless the test goes and looks for the key's absence on purpose.

**Guard form that survives:**
- For a consumer gated on `A OR B`, write a fixture that makes exactly one of them true and the
  other false, and assert the consumer's output still reflects the OR — not just a fixture where
  both happen to be true together. Here: drive `finished_edge` true (a `was == BUSY, now ==
  IDLE` transition in one tick, with `IDLE_CONFIRM_SECONDS` collapsed) while forcing
  `finished_evidence` false (`last_assistant_activity_at` stubbed to `None`, so `did_work_since`
  cannot be true regardless of `since`), and assert the excerpt still appears.
- For a conditional write (`if guard: obj[key] = value`), pair the existing "value is correct
  when guard holds" test with one that drives the guard to be false and asserts `key not in obj`
  — not just that some other, unrelated field (like the summary line) looks right. A value
  computed from `x or default` inside the write can make the guard's `if` vacuously always take
  the branch, and only an explicit membership check on the *object*, not the value, catches that.

**Found:** `chela/inbox.py`'s `agent_events` call site (`said = final_message(wid)`) and its
payload write (`if said: payload["final_message"] = ...`) (CMX-318 rework round 5, PR #396).
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3348 passed) under both mutations
above, applied independently, in a throwaway checkout of the PR head. Closed by
`test_the_finished_notice_quotes_the_agent_on_the_ordinary_edge_path_alone` (drives
`finished_edge` true with `finished_evidence` forced false, in one tick) and
`test_no_final_message_key_is_written_when_the_agent_said_nothing` (asserts `"final_message" not
in payload` on the said-nothing fallback, via the queued/busy-orchestrator path so the payload is
inspectable).
