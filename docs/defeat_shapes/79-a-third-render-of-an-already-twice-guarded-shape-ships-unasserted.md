## 79. A third render of an already-twice-guarded shape ships unasserted

**Assertion form:** a component renders the same "empty state" markup shape (a
`.diff-patch-empty` div with placeholder text) at three call sites. Two of those call sites
(a `Loading…` write at open-time and another at fetch-time) already went through a prior
rework round and got a guard, closing the *pattern's* mutation once. The third call site — the
patch pane's very first render, before any file row is ever clicked — renders the same shape
but was never independently asserted; its coverage was assumed to come along with the other
two for free.

**Mutation that defeats it:** blank the third site's text
(`<div class="diff-patch-empty">Select a file to view its diff.</div>` →
`<div class="diff-patch-empty"></div>`). Every existing assertion in the suite overwrites that
pane with a fetched patch before ever reading it (the tests click a file row and check the
patch view afterward), so the blank initial render is never observed and the suite stays green
— `grep -rn 'Select a file' tests/` returns nothing.

**Guard form that survives:** read the pane's content immediately after the modal opens, BEFORE
the first simulated file-row click — the one moment the initial-render text is actually the
pane's content and hasn't yet been overwritten by a later assertion's own setup.

**Found:** CMX-299 rework round 10 (2026-08-17), PR #373. Same test file/round as
[[78|shape 78]]. Closed by asserting `patchView.textContent.trim() === 'Select a file to view
its diff.'` right after `await flush()` on modal-open, before the row-click block that follows
it.

**Found again, a Python f-string instance of the same shape:** CMX-321 rework round 4, PR
#409. `chela/runtime_truth.py::_installed_report`'s gone-marketplace `ERROR` finding renders
`copy.marketplace` through an f-string at three sites in one message: "the installed
plugin's marketplace {copy.marketplace!r} is GONE", "`claude plugin list` reports
`chela@{copy.marketplace}`", and, inside the fix instruction itself, `` `claude plugin
marketplace add <path-or-url-to-the-{copy.marketplace}-marketplace>` ``. Round 2 pinned the
first two with phrase assertions (`"marketplace 'acme' is GONE"`, `"chela@acme"`); the third
was covered only by the bare substring `"claude plugin marketplace add" in body`, which a
blanked slug still satisfies. The judge's required-mutation-set verdict blanked exactly that
site (`{copy.marketplace}` → `{''}`) and the suite stayed green. Closed by asserting
`"path-or-url-to-the-acme-marketplace" in body` in
`test_doctor_ERRORs_when_the_marketplace_is_gone`, `tests/test_installed_plugin.py`. (The
"mirrored" `chela plugin` test, `test_chela_plugin_names_a_gone_marketplace_distinctly_from_a_stale_install`,
exercises a *different* message built independently in `chela/main.py` that never
interpolates the slug into this instruction at all — `` `claude plugin marketplace add
<path-or-url>` `` — so it was not a third render site and needed no change here.)

**Found a third time, at sibling sites in the same two messages — a different value each
time, so pinning the slug did not close it:** CMX-321 rework round 5, PR #409. Round 4's fix
above pinned `copy.marketplace` at its fourth render site in `_installed_report`'s message,
but two OTHER f-string interpolations in the very same two "gone marketplace" messages were
never pinned by any round: `chela/main.py::_report_installed_plugin`'s ⛔ line names WHICH
manifest will not load (`f"Claude Code will not load {copy.manifest} AT ALL"`), and
`chela/runtime_truth.py::_installed_report`'s detail names the registry file the verdict was
read from (`f"own registry ({hooks.plugins_dir() / 'known_marketplaces.json'})"`). Both are
sibling interpolations to ones already pinned in the same message — exactly this shape, just
a different variable each time — so a reviewer checking "is the shape 79 spot closed" for
`copy.marketplace` walked right past two more unrelated blank-able values sitting in the
same sentence. The judge's required-mutation-set verdict blanked both (`{copy.manifest}` →
`{''}`, `{hooks.plugins_dir() / 'known_marketplaces.json'}` → `{''}`) and the suite stayed
green on both. Closed by asserting the manifest path (`str(root / "hooks" / "hooks.json")`)
in `test_chela_plugin_names_a_gone_marketplace_distinctly_from_a_stale_install` and the
registry path (`str(hooks.plugins_dir() / "known_marketplaces.json")`) in
`test_doctor_ERRORs_when_the_marketplace_is_gone`, both in `tests/test_installed_plugin.py`.
**Lesson:** closing shape 79 for one interpolated value in a message does not mean the
message is closed — every distinct `{...}` render site in that message needs its own
assertion, not just the one the current round happened to name.

**Found a fourth time, across separate FILES instead of separate render sites in one
message:** CMX-368 rework round 2, PR #512. The same "use `chela request-push`, never
`git push` yourself" procedural instruction is independently authored in (at least) four
places: `chela/starter.py`'s seeded `WORKFLOW.md` template (an adopter's freshly-seeded
copy), `chela/dispatcher.REWORK_PROMPT` (the brief a reworking agent reads, rendered
through `_renudge_prompt`/`_respawn_rework`), chelamux's own repo-root `WORKFLOW.md`
(the hot-reloaded Done Criteria this repo's own dispatched agents read), and two more
adopter-facing docs (`examples/WORKFLOW.md`, `skills/chela-setup/SKILL.md`). Round 1
pinned only the first copy (`tests/test_starter.py`), on the reasoning that it was "the"
production template; the judge's round-2 verdict reverted the REWORK_PROMPT and
repo-root-WORKFLOW.md copies straight back to the pre-fix `git push` wording and the
suite stayed green, because nothing read either of those two files/constants at all.
Unlike the f-string case above, these aren't render sites of one interpolated value in
one message — they're wholesale independent copies of the same paragraph in different
files, each reachable only through its own call site (`_renudge_prompt` for the
constant, a plain file read for the markdown). Closed by adding one test per remaining
*live* copy (production code or this repo's own dogfooded config — not the two
adopter-facing docs, which are advisory-only notes for now): `test_rework_prompt_step_4_tells_the_agent_to_request_push_not_git_push`
renders `REWORK_PROMPT` through `_renudge_prompt` and pins the literal
`chela request-push`/`Do NOT git push` wording;
`test_chelamuxs_own_workflow_md_tells_agents_to_request_push_not_git_push` reads the
repo's own `WORKFLOW.md` off disk directly and pins the same, both in `tests/test_judge.py`.
**Lesson:** "the same guarded string, rendered more than once" is not just one message's
multiple interpolation sites — it is also one paragraph of *prose*, hand-copied into
every file/constant that needs to say the same thing to a different reader. Pinning the
first copy you find does not mean the others are covered; each file that carries its own
copy needs its own test reading *that* file, because nothing else exercises it.

**Found a fifth time — closing three of five named copies is the same failure as closing
one, it just moves the edge:** CMX-368 rework round 3, PR #512. Round 2's fix (above) pinned
three of the five known copies with three hand-named test functions, and its own commit
message called the two adopter-facing docs (`examples/WORKFLOW.md`,
`skills/chela-setup/SKILL.md`) out by name as deliberately left unpinned ("advisory-only
notes for now"). The judge's round-3 verdict reverted exactly those two, unaltered, and the
suite stayed green — `grep -rn 'examples/WORKFLOW\|skills/' tests/` returned nothing at all.
Three named-file assertions closed three call sites; the other two, being reachable only
through their own file read, were exactly as unguarded as before round 2 ran. **Naming the
narrowing in the catalog entry (as round 2's own text above did) does not close it** — a
documented gap is still a gap.

Closed differently this time, on the reviewer's explicit instruction: not a fourth and fifth
hand-named test function (which would only re-run this same shape on copy six), but ONE
table — `tests/test_push_briefs.py::PUSH_BRIEFS`, a list of `(name, get_text)` pairs
covering all five copies (three file reads, two in-code renders) — and ONE parametrized test
asserting every entry routes through `chela request-push` and contains no bare `git push -u
origin`/`gh pr create --base` instruction. The acceptance criterion the reviewer stated
directly: "adding a sixth copy of this brief tomorrow is covered without editing the test" —
i.e. the guard's *closure* must not depend on a human remembering to write test number N+1
next time a brief gets copied to a new file.

**Lesson, sharpened:** when the SAME instance of this shape recurs across separate call
sites more than twice, adding one more named test per recurrence is not converging — it is
the shape happening again with the count incremented. The durable fix is a data structure
that enumerates every known instance in one place, plus one guard that iterates it, so the
next instance is an entry in a list rather than a new test function nobody is forced to
write.
