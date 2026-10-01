# Judge-eval rubric v1 — is a mutation REALISTIC or CONTRIVED?

This rubric is read verbatim by the judge-eval grader (`chela/judge_eval/grade.py`) and is
the written standard the operator spot-checks against. It grades ONE proposed mutation
(`file`, `before` → `after`) of production code, the way the chela judge proposes them.

## 1. The question

A mutation is **realistic** when a plausible FUTURE edit to this code — a refactor, a
feature change, a hurried fix, a merge resolved the wrong way — could produce the same
breakage. A test that fails to catch a realistic mutation is a real gap: the next ordinary
change can slip through it.

A mutation is **contrived** when only a targeted, unrealistic corruption produces it — an
edit someone would write only to dodge or trip one specific test. A test that misses it
has a gap no ordinary edit will hit, so blocking a PR on it costs a rework round for no
protection.

For a historical finding the same test decides the label: **real** = realistic (it exposed
a gap a plausible future edit would hit, or one that later caused a real bug); **contrived**
= contrived.

## 2. Shapes

Name the shape of the change (the `shape` claim). Two shapes are contrived BY DEFINITION:

- `special_case_injection` — adds a branch keyed on one specific input value (`if name ==
  "x": return …`) that the original code never special-cased.
- `targeted_literal` — changes a literal to another arbitrary non-empty value that only
  matters because a test asserts the original (a message string rewritten, a magic number
  nudged) where no ordinary edit would touch it.

The others are judged by the claims in §3:
`flip_condition` (an inverted comparison / boolean / negation), `disable_check` (a guard
short-circuited off), `drop_call` (a call or statement removed), `empty_value` (a value
emptied to None / "" / [] / 0), `revert_callsite` (the production wiring reverted),
`off_by_one`, `swap_order` (two steps reordered), `narrow_set` (an item dropped from a
list, set, regex alternation or allow-list), `change_constant` (a threshold, interval or
limit changed), `rewrite_logic` (the logic replaced by a different, still-plausible
version), `other`.

## 3. The claims — answer each literally

- `changed_code_quote` — copy, VERBATIM from `before`, the exact code the mutation
  changes. A grade whose quote is not in `before` is discarded as ungraded.
- `ordinary_edit` — true if a developer doing ordinary work on this code (not trying to
  break a test) could plausibly produce this exact change: dropping an item from a list
  while editing it, reverting a line in a bad merge, forgetting a call when refactoring,
  getting a comparison backwards, caching a value that must be re-read.
- `requires_test_knowledge` — true if the edit only makes sense to someone who knows what
  a specific test asserts (it is aimed at a fixture value, a test's exact string, a
  sentinel only the test uses).
- `main_path` — true if the mutated code runs on the feature's ordinary path, not only on
  a rare error or edge branch. Recorded for analysis; it does not decide the label.
- `rationale` — one sentence.

## 4. The decision (applied in code, not by the grader)

1. shape is `special_case_injection` or `targeted_literal` → **contrived**
2. `requires_test_knowledge` → **contrived**
3. not `ordinary_edit` → **contrived**
4. otherwise → **realistic**

Some shapes are decided without the LLM (`grade.classify_programmatic`): a single flipped
operator or negation, a value emptied, a statement commented out, and a guard
short-circuited off are realistic; a new comparison against a literal the code never
made is `special_case_injection`. A `wiring` revert always goes to the LLM, because a
revert can be an ordinary lost line or a surgical one.

## 5. Worked examples

- `if not isinstance(sid, str) or not SESSION_RE.match(sid):` → `if not isinstance(sid,
  str):` — `drop_call` / `narrow_set`; dropping one clause of a validation during a
  refactor is ordinary → **realistic**.
- `r"(?:WHY|OBJECTIVE|GUARDS|VERIFY|NOTES?)"` → `r"(?:WHY|OBJECTIVE|VERIFY|NOTES?)"` —
  `narrow_set`; editing an alternation and losing an item is ordinary → **realistic**.
- `.canvas { grid-column: 2; grid-row: 1;` → `grid-row: 2;` — `change_constant`; a
  leftover value from a removed layout row is exactly how it broke → **realistic**.
- `return label` → `return label if label != "Fixture Task 7" else ""` —
  `special_case_injection`; only a test's fixture value makes it matter → **contrived**.
- `notice = "typing is off for guests"` → `notice = "typing is off for guestz"` —
  `targeted_literal`; nobody edits one letter of a message except to trip the test that
  pins it → **contrived**.
