## 342d. A conditional's truthiness and the value it renders are two separate variables — asserting the rendered content says nothing about which variable gates it

**Assertion form:** the summary f-string in `acknowledge_blocked_race` pairs a **gate**
(the condition deciding *whether* a clause appears) with a **payload** (the value rendered
*inside* that clause) that are the raw/stripped forms of the same argument:
`(f" — {clean_note}" if clean_note else "")`. [[342b|shape 342b]] added
`test_acknowledge_event_summary_renders_the_task_id_who_and_note` (asserts the note's text
appears when a real note is given) and
`test_acknowledge_event_summary_omits_the_dash_when_there_is_no_note` (asserts no dash when
`note` is omitted entirely, so both `note` and `clean_note` are falsy — `None` and `""`).
Neither fixture ever makes the gate and the payload **disagree**: the omitted-note fixture
never reaches a state where `note` is truthy but `clean_note` is empty, so a mutation that
reads the gate from the wrong variable (`if note` instead of `if clean_note`) renders the
exact same output — no dash, an empty dash, whichever — on every fixture the file had.

**Mutation that defeats it:** swap the gate's variable for its raw twin —
`(f" — {clean_note}" if clean_note else "")` becomes `(f" — {clean_note}" if note else "")`.
Feed a **whitespace-only** note (`"   "`): `clean_note = (note or "").strip()` reduces it to
`""` (falsy — the DB column and the payload's `"note"` key both already record `""` for this
input, per the pre-existing `.strip()` call), but the raw `note` string `"   "` itself stays
truthy. The mutated gate fires, and the f-string renders `f" — {clean_note}"` with
`clean_note` still `""` — a dangling `" — "` separator with nothing after it, into an
operator-facing notification that has nowhere else recording "there is no note" for a reader
to cross-check against. Both existing 342b tests stay green: the "renders the note" test
never passes a note that's all whitespace, and the "omits the dash" test passes no note at
all, so `note` is `None` (falsy) there too — neither fixture ever makes `note` truthy while
`clean_note` is empty.

**Guard form that survives:** for any `if X else` gate whose *branch content* is a
transformed form of `X` (a `.strip()`, a default-substitution, a cast), treat the gate and
the content as two separate variables to separate, per [[342|shape 342]]'s general question —
and specifically pick a fixture where the **untransformed** form is truthy while the
**transformed** form is falsy (here: whitespace-only input strips to empty). Asserting only
"the dash is missing when no note is given" and "the note's text appears when a note is
given" leaves the all-whitespace cell of that 2×2 unexercised; the fix here adds
`test_acknowledge_event_summary_omits_the_dash_for_a_whitespace_only_note`, which acknowledges
with `note="   "` and asserts the summary contains no `"—"` — closing the cell where the raw
argument is truthy but its cleaned form is not.

**Why this needed its own entry, not folding into [[342b|shape 342b]] or [[342c|shape
342c]]:** 342b is about a sink never being read at all; 342c is about two *whole arguments*
(`ident` vs. `task_id`, `by` vs. `who`) that coincide because a fixture always supplies them
identically. This entry is neither — it's a single argument's **gate** vs. its own
**rendered content** disagreeing only for a narrow input class (whitespace-only strings) that
neither prior fix's fixtures ever constructed, on the same f-string those two entries already
covers the other parts of.

**Found:** CMX-342 (PR #445), judge round 3, immediately after round 2 closed [[342c|shape
342c]]. `chela/dispatcher.py`'s `acknowledge_blocked_race` summary f-string; the judge
swapped the note clause's guard from `if clean_note` to `if note` in a throwaway checkout of
the PR head, and `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3699 passed) stayed green.
Closed by `test_acknowledge_event_summary_omits_the_dash_for_a_whitespace_only_note`
(`tests/test_dispatcher_blocked_race_ack.py`), which acknowledges with a whitespace-only
note and asserts the summary carries no note separator.

**See also:** [[342|shape 342]] — the general "two quantities that coincide in every
fixture" family this is a further instance of, on the same function, one round later still.
[[342b|shape 342b]] and [[342c|shape 342c]] — the earlier rounds on this same summary
f-string, closing the "sink unread" and "whole-argument coincidence" gaps this entry's gate/
content gap survived both of.
