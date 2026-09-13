## 363c. "Covered as a class" is not a measurement — the last two sampled-off siblings still had to be mounted

**Assertion form:** round 2's rework (shape [[363b|363b]]) closed the two branches its own
judge round happened to sample (the per-call reset, `gh` missing/timing out) and then wrote
off the remaining two `read_failed = True` assignments — repo unresolvable (`gh_issues.py:155`)
and unparseable JSON (`:195`) — as "left as the sampled-class case per [[311|shape 311]], not
separately mounted here." Shape 311 names a trap ("a control written for one sibling
implementation is never mirrored onto a structurally identical second"); it does not license
stopping once *some* sibling has a test. Two structurally-identical-looking branches are still
two different lines of code, and only a mutation on the actual line proves the assertion
reaches it. The round-2 rework treated "identical shape" as equivalent to "already measured" —
it wasn't.

**Mutation that defeats it:** two independent one-line mutations on the two branches the
round-2 rework declined to mount, both invisible to the round-2 suite:
1. `chela/sources/gh_issues.py:155` — `self.read_failed = True` → `self.read_failed = False`
   in the `if not repo:` branch (repo unresolvable via both `gh repo view` and the git-remote
   fallback failing).
2. `chela/sources/gh_issues.py:195` — `self.read_failed = True` → `self.read_failed = False`
   in the `except (json.JSONDecodeError, ValueError)` branch (unparseable `gh issue list`
   output).

**Guard form that survives:** stop sampling — enumerate. A single test parametrized over the
COMPLETE set of `read_failed = True` assignments in `list_open_tasks()` (repo unresolvable,
config_error, `gh` missing/timeout, non-zero exit, bad JSON — five branches, five parameters,
no sixth left over), each asserting `src.read_failed is True` after a call built to land in
that exact branch (`test_every_read_failed_true_branch_actually_sets_the_flag`,
`tests/test_gh_issues_allowlist.py`). Enumerating rather than sampling means there is no
"structurally identical sibling" left for a future round to defer again.

**Found:** CMX-363 rework round 3 (2026-09-13), PR #498 — judge's required-mutation-set
verdict; both mutations SURVIVED the round-2 suite (`CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q`, 3969 passed / 0 failed both before and with each mutation applied). Same root
family as [[363|363]] and [[363b|363b]]: a multi-branch flag gets tests for whichever
branches a review pass happened to look at, not for the full set the PR actually touches —
this round's twist is that the deferral was *explicit and cited a shape by name*, which reads
as more rigorous than silence but is not: [[311|shape 311]] is a description of the failure
mode, not a stopping rule. "It's the same shape as one I already tested" is not evidence that
this line runs under test — only a mutation on this line is.
