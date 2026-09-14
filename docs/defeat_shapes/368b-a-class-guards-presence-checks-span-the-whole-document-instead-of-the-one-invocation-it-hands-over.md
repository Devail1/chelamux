## 368b. A class guard's presence checks span the whole document instead of the one invocation it hands over

**Assertion form:** a table-driven guard runs the same three checks over every brief in a
class (`PUSH_BRIEFS`): a required phrase is present *somewhere* in the text
(`"chela request-push" in text`), and two forbidden phrases are absent *anywhere* in the text
(`"git push -u origin" not in text`, `"gh pr create --base" not in text`). Each check reads
as pinning "the brief tells the agent to do the right thing" — but `in`/`not in` against the
*whole document* can't see which specific command line those tokens belong to. A brief with
five paragraphs and one backtick-quoted command line satisfies all three checks as long as
the command's *name* is right anywhere in the file; nothing ties the checks to the actual
invocation the reader will copy and run.

**Mutation that defeats it:** delete two flags from the one command line that matters, in one
of the two briefs the guard exists specifically to cover:

```diff
-    `chela request-push {{task_id}} --pr-title "{{project_key}}-{{task_number}}: <summary>" --pr-body-file <path>`
+    `chela request-push {{task_id}}`
```

`"chela request-push" in text` is still true (the shortened line still contains it).
`"git push -u origin" not in text` and `"gh pr create --base" not in text` are both still
true (neither forbidden phrase was ever in this brief, mutated or not). All three assertions
pass — `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (4046 passed) with the
mutation applied — while the brief now hands a first-dispatch agent an invocation with no
`--pr-title`. `dispatcher._apply_push_request`'s `if not pr_url and pr_title:` gate never
fires for that run, the branch is pushed, no PR is ever opened, and the run reaches
`awaiting_review` with an empty `pr_url` — the exact state the marker-before-task-finished
ordering (and the PR that introduced this whole brief) exists to prevent.

**Why this slips through even though the checks look like they cover "the right command":**
none of the three assertions is anchored to a *span* — each is a document-wide
presence/absence search. A guard shaped like this can prove a brief mentions the right verb
and doesn't mention the wrong ones, but it cannot tell "the required flags are on the
invocation" apart from "the required flags are missing from the invocation, and something
else in the document happens to mention the verb". Two briefs in the same class
(`WORKFLOW.md`, `starter.py`'s template) already had *separate*, hand-named tests pinning
their exact command line byte-for-byte — those two were never at risk. The class guard was
introduced specifically to extend that coverage to the two briefs that had none
(`examples/WORKFLOW.md`, `skills/chela-setup/SKILL.md`), and it extended the wrong property:
presence-of-tokens instead of shape-of-invocation.

**Guard form that survives:** extract the single backtick-quoted span that starts with the
command name (`re.search(r"`chela request-push[^`]*`", text)`) — the actual invocation the
brief hands over — and assert the required/forbidden properties against *that span*, not the
surrounding document. This class of command also has two genuinely different correct shapes
(a first-dispatch invocation must carry `--pr-title`/`--pr-body-file`; a rework invocation
must carry neither, because the PR already exists), so a per-entry `kind` distinguishing the
two is part of the fix — a single shared assertion can't be right for both without also being
satisfiable by dropping the flags from either one.

**Found:** `tests/test_push_briefs.py`'s
`test_every_push_brief_routes_through_chela_request_push` (CMX-368 round 4, PR #512) —
closed by matching the backtick-quoted invocation span per entry and asserting `--pr-title`/
`--pr-body-file` presence (first-dispatch briefs) or `--pr-title` absence (the rework brief)
against that span specifically, instead of the whole brief text.
