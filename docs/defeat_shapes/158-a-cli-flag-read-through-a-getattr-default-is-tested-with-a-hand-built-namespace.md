## 158. A CLI flag read through a `getattr(args, "flag", False)` default is tested only with a hand-built Namespace

**Assertion form:** a new flag (`chela release X.Y.Z --major-tag-only`) changes which branch the
handler takes. The handler reads it defensively — `if getattr(args, "major_tag_only", False):` —
so older callers that build the Namespace without it still work. The test calls
`main.cmd_release(SimpleNamespace(..., major_tag_only=True))` and asserts the major-tag branch
ran. It passes, and it really does exercise the branch.

**Mutation that defeats it:** rename the argparse dest so the real parser no longer produces
the attribute the handler reads:

```diff
- p_release.add_argument("--major-tag-only", action="store_true",
+ p_release.add_argument("--major-tag-only", dest="major_tag", action="store_true",
```

The suite stays green (8315 passed). The test builds its own Namespace with the right attribute
name, so it never meets the parser. In production, `--major-tag-only` now sets `args.major_tag`,
the `getattr` default answers `False`, and the command silently runs a FULL release prepare
instead of the one-tag move the operator asked for.

**Why this is distinct from [[368|shape 368]]:** in 368 nothing drove the parser at all, and the
mutation unplugged a whole subcommand. Here the subcommand IS driven through `main.main()`
elsewhere (`test_chela_release_is_wired_into_the_command_line`), so the dispatch line is covered.
What is uncovered is one flag's dest, and the `getattr` default is what makes it silent: a
plain `args.major_tag_only` would raise `AttributeError` under the mutation, and the existing
argv test would go red. A defensive default turns a wiring break into a wrong-branch run.

**Guard form that survives:** for each flag that selects a branch, drive it through the real
parser (`sys.argv = [...]`, `main.main()`), stub the branch's callee to record the call, AND
stub the OTHER branch's callee to `pytest.fail`. The second stub is the part that catches the
fall-through.

**Found:** CMX-158 rework round 1 (2026-10-16), judge review of PR #710. Closed by
`tests/test_release.py::test_major_tag_only_on_the_real_command_line_reaches_the_major_tag_branch`,
which turns red under the mutation above.
