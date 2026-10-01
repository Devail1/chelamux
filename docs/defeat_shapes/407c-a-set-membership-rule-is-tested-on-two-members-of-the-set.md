## 407c. A set-membership rule is tested on two members of the set

**Assertion form:** the code refuses (or accepts) anything in a SET — a character class, a
tuple of names, a list of fallback candidates — and the test checks two or three
representative members: `extendable("pytest && npm test")` and `extendable("pytest | tee")`
are both refused. It reads as complete because each assertion is a real, motivating case.

**Mutation that defeats it:** shrink the set to exactly the members the test names.
`_SHELL_META = r"[;&|<>`]|\$\("` → `r"[&|]"` keeps both refusals and drops five others
(`;`, `<`, `>`, a backtick, `$(`); `("pytest", "py.test")` → `("pytest",)`; the JS resolver's
`(base, base + ".js", base + ".mjs")` → `(base,)`. All green (CMX-407 round 4 self-check).

A boundary set has a second side that is just as easy to miss: the members the code must
ACCEPT. `Path(tok).name` → `tok` broke `/usr/local/bin/pytest` and nothing noticed, because
every accepted spelling in the test was a bare `pytest` or a `-m pytest`.

**Guard form that survives:** one parametrized case PER MEMBER, on both sides — every
character in the class refused on its own, every accepted spelling (bare, by path, `py.test`,
`-m pytest`) accepted on its own, plus the near-misses that must stay out (`pytestx`,
`-m pytestx`, a dangling `-m`). Then any member removed or added turns exactly one case red.

**Found:** CMX-407 round 4 (2026-10-01), PR #558 — `chela/judge_select.py::extendable` and
`_resolve_js`. Closed by
`tests/test_judge_select.py::test_every_shell_construct_and_non_pytest_is_NOT_extendable`,
`test_every_plain_pytest_spelling_IS_extendable` and
`test_js_specifiers_resolve_with_and_without_an_extension`.

**A second-order trap found closing it:** a judge test that echoes the nested verdict's
`reason` as its assertion message (`assert o.subset_verdict == INVALID, o.reason`) prints
"went red with 2 error(s)" when it fails — and the judge measuring THIS suite parses that
failure output with the same `(\d+) errors?` regex. The guard fired, but the outer judge
filed it INVALID ("a file that no longer loads"). Assert with a message that names verdicts,
never counts (`_why(o)` in `tests/test_judge_select.py`).

**Related:** [407b](407b-an-only-x-is-final-rule-is-tested-on-two-of-a-three-valued-outcome.md)
is the same blind spot over an enum's values rather than a set's members — the test covers
the story the author had in mind, not the domain the code actually branches on.
