## 34. A `.gitignore` rule shipped with nothing asserting it actually matches

**Assertion form:** none. The fix is a new pattern line in `.gitignore` — a config file, not
code — so it reads as self-evidently correct: the pattern is right there, syntactically
valid, and a human skimming the diff confirms it "looks like" it matches the filename it's
meant to exclude. Nothing in the suite ever asks git whether it actually does.

**Mutation that defeats it:** edit the pattern to something that stays syntactically valid
but no longer matches the real filename it was written for — e.g. append a literal token
into the middle of a glob (`.chela-self-check-*.json` → `.chela-self-check-DISABLED-*.json`).
The line still parses as a gitignore rule, so there is no parse error to catch, and since
nothing runs `git check-ignore` against a real instance of the pattern, the full suite stays
green with the rule silently dead.

**Guard form that survives:** drive the real mechanism — `git check-ignore -q -- <a realistic
instance of the pattern>` against the repo's own `.gitignore` (not a throwaway repo built
inside the test) — and assert the exit code. This is the same asymmetry as shape 5 (source
constant vs. rendered value): the pattern *text* existing is not the same claim as the
pattern *matching*, and only asking git proves the second one.

**Found:** CMX-286 rework round 2 (2026-08-14), PR #357. The PR's `.gitignore` fix for
`.chela-self-check-*.json` (added to stop `WORKFLOW.md` step 6's throwaway experiments file
from landing on `dev` a third time) shipped with no guard at all — matching shape 9's
category, but specific to config files rather than code: there was no call site to revert,
only a pattern to silently de-fang. The judge's mutation above kept the file green.
Closed by `tests/test_gitignore_scratch_files.py`, which shells out to `git check-ignore`
against a real `.chela-self-check-<task>.json` name and asserts exit code 0.
