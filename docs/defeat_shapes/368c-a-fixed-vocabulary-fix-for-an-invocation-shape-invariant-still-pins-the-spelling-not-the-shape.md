## 368c. A "fix the vocabulary, not the shape" round's own fix is still vocabulary — twice, in the same guard

**Assertion form:** round 4 of the same guard (see [[368b|shape 368b]]) correctly diagnosed
that its predecessor pinned vocabulary instead of the invocation, and replaced document-wide
presence/absence checks with a check anchored to the one backtick-quoted `chela request-push
...` span each brief hands over. But two of that fix's own assertions stayed exactly as
vocabulary-shaped as what they replaced, just narrower in scope:

1. The absence checks kept their round-3 literal spellings — `"git push -u origin" not in
   text` and `"gh pr create --base" not in text` — instead of becoming shape-based. The
   INVARIANT is "no runnable `git push`/`gh pr create` invocation anywhere in the brief"; the
   assertion still only catches the one flag spelling (`-u origin`, `--base`) it happened to
   be written against.
2. The invocation-span check asserted which *flags* the span carries (`--pr-title`,
   `--pr-body-file`) but never asserted its own REQUIRED *positional* (`{{task_id}}`) was
   still there — an omission a flag-only check can't see because a missing positional doesn't
   remove any flag.

**Mutation that defeats it:** two independent mutations, one per assertion.

Finding 1 — the absence checks:

```diff
- 4. **Request the push and PR — do NOT run `git push` or `gh pr create` yourself.** ...
+ 4. **Push your branch, then ask chela to open the PR.** Run
+    `git push origin {{branch_name}}` yourself, then write your PR body to a file and run: ...
```

`` `git push origin {{branch_name}}` `` contains neither forbidden substring (`git push -u
origin`, `gh pr create --base`) — no `-u`, no `--base` — so both absence checks stay true.
The presence check (`"chela request-push" in text`) and the invocation-span flag checks also
stay true, because the brief's `chela request-push ...` line is untouched; the new
instruction is simply inserted *before* it. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
stayed green (4046 passed) while the brief now tells a first-dispatch agent to run `git push`
itself — exactly the sandboxed-token failure (`Invalid username or token`) issue #502 B2
exists to prevent.

Finding 2 — the invocation-span positional:

```diff
-    `chela request-push {{task_id}} --pr-title "..." --pr-body-file <path>`
+    `chela request-push --pr-title "..." --pr-body-file <path>`
```

The extracted span still contains `--pr-title` and `--pr-body-file` — both flag assertions
pass. Nothing in the guard ever looked at what comes immediately after `request-push`.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (4046 passed) while the invocation
the brief hands over is missing its one required positional; `chela request-push` (argparse)
refuses with "the following arguments are required: task_id" and exits 2 — no marker is ever
written, so the daemon never pushes and never opens a PR.

**Why round 4's own diagnosis didn't prevent this:** round 4 correctly moved the *scope* of
the checks from "the whole document" to "the one invocation span" — that closed shape 368b.
It did not also revisit the *content* of what those checks tested for. A check narrowed to
the right span can still test the wrong property: "this substring is absent" and "this
substring is present" are both still vocabulary-shaped assertions, just applied to a smaller
piece of text. Narrowing scope and generalizing content are two independent fixes; doing only
the first leaves the second bug intact, one level down.

**Guard form that survives:**
- For a forbidden command, match the *invocation shape* (any backtick-quoted span starting
  with the verb and carrying at least one argument — `` `git push +\S[^`]*` ``), not one flag
  spelling. A bare, argument-less mention (`` `git push` ``, as it appears in the brief's own
  prohibition sentence) doesn't match; an invocation with any arguments does, regardless of
  which flags.
- For a required command's own invocation, once the span is extracted, additionally split it
  into tokens and assert its own required *positional* argument is present and isn't itself a
  flag — a flag-presence check alone can prove the flags are there without proving the
  positional wasn't dropped.

**Found:** `tests/test_push_briefs.py`'s `test_every_push_brief_routes_through_chela_request_push`
(CMX-368 round 5, PR #512) — both mutations applied to a throwaway checkout of the PR head,
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` green (4046 passed, 0 failed) under each.
Closed by replacing the two fixed-substring absence checks with `_FORBIDDEN_GIT_PUSH_INVOCATION`
/ `_FORBIDDEN_GH_PR_CREATE_INVOCATION` (regexes matching any backtick-quoted invocation with an
argument) and adding a positional-token assertion on the extracted `chela request-push ...`
span, ahead of the existing per-`kind` flag checks.
