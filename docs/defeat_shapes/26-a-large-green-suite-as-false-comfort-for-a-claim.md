## 26. A large green suite as false comfort for a claim that has zero guard of its own

**Assertion form:** a PR's own description (or test-plan section) states a specific
behavioral claim — often "the default X moves from A to B" — that depends on one or more
small, easy-to-miss literals (a fallback return value, a pre-set CSS class in a template, a
`let` initializer). The PR ships alongside a large, genuinely-passing test suite covering
the surrounding feature, and that suite's size and greenness reads as coverage — but none of
its tests ever reads back the actual runtime/rendered consequence of the specific literal
the claim depends on. This is subtler than shape 9 ("no guard at all, and the PR says so"):
here the PR believes it shipped tests for the change, and did — just not for this claim.

**Mutation that defeats it:** revert any one of the claim's load-bearing literals to its
pre-change value, in isolation. Every test in the surrounding (large, real, honest) suite
was written against the FEATURE, not against this specific default, so none of them ever
drives execution through the literal's actual consequence — the suite's size is irrelevant
to whether this one fact is pinned.

**Guard form that survives:** for every literal a PR's own claim names as load-bearing,
write a guard that reads its RENDERED/runtime consequence (a live DOM node's class list, an
imported module's live binding, a downstream render that only fires if the value is right)
— never a re-parse of the literal itself — and manually revert the literal once to confirm
the new guard actually goes red before trusting it. A claim stated only in prose (a PR
description, a code comment, a test-plan bullet) is not evidence it was ever tested; treat
it as a checklist of guards still owed.

**Found:** CMX-279 rework round 1 (2026-08-13), PR #350 — three independent literals all
backed the PR's own claim that "the default view (when the wall is off) moves from Agents to
Work": util.js's `let currentTab = 'work';` initializer, index.html's pre-set
`class="panel active"` on `#panel-work`, and nav.js's `_agentDetailBackView()` fallback.
3059 tests passed, none of which ever booted `main.js` with a genuine terminals-off
bootstrap and read back `currentTab`, `#panel-work`'s classList, or the agent-detail "←
Back" link's actual target. The judge reverted each literal independently, in a throwaway
checkout, and the full 3059-test suite stayed green all three times. Closed by
`tests/dashboard_default_view.test.mjs` (a real terminals-off `main.js` boot reading the
live `currentTab` binding, the real `#panel-work` DOM node, and the `renderKanban()` paint
that only happens if the `currentTab` gate at `work.js:174` actually lets it through) plus
two new assertions extending `tests/sidebar.test.mjs`'s existing agent-detail drill-in tests
(covering both `_agentDetailBackView()` call sites — nav.js:560 and :608 — on the
terminals-on branch, the counterpart to the terminals-off branch the new file covers).
