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
