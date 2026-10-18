## 176. A lint config is proven only by running it over a tree that already obeys it

**Assertion form:** a boundary/lint/type config (`.dependency-cruiser.cjs`, `tsconfig.json`,
`eslint.config.js`) is "guarded" by a CI step that runs the tool over the repo's own source,
plus a negative control done once by hand (a deliberate deep import, a deliberate type
error) and reverted before commit. The step is green and the PR says the rules were seen to
bite, so it reads as covered.

**Mutation that defeats it:** weaken the rule's pattern so it can never match —
`const INTERNALS = '^src/(?:features|shared)/[^/]+/[^/]+/'` →
`'^src/(?:features|shared)/[^/]+/[^/]+/__never__/'`. Every boundary rule built on it is now
inert, and the clean tree still produces zero violations, as it did before. The same holds
for `"strict": false`, a rule set dropped from the ESLint config, or `severity: 'ignore'`
on the cycle rule: a tree with no violations passes every one of them.

**Why it looks covered:** "the tool runs in CI and is green" proves only that the source
obeys the config, never that the config forbids anything. The hand-run negative control
proved it once, on a commit that no longer exists; nothing re-runs it under the next change.

**Guard form that survives:** a committed test builds a throwaway tree with one violation
per rule (and the allowed imports next to them), runs the real tool against the real config
file, and asserts the EXACT set of `(rule, from, to)` violations, or the exact set of files
`tsc`/`eslint` reject. Exact-set equality catches both directions: a rule that stops biting
drops a row, and a rule that over-tightens adds one.

**Found:** CMX-176 judge round 1, PR #751. Fixed in `tests/test_frontend.py`
(since 176b: the `pnpm`-run gate tests over `tests/fixtures/frontend_gates/`).
