## 209. A source-precedence test feeds the authoritative source only the refusal answer

**Assertion form:** a reader has two sources and one wins — an open PR's body is read from
GitHub, the pending push marker only when no PR exists. The guard proves the precedence by
making the sources disagree: the marker says *reviewed*, GitHub says *not reviewed*, and the
test asserts the gate REFUSES. It also pins the exact `gh pr view …` argv, so it reads as
proving "GitHub was asked, and GitHub's answer was used".

**Mutation that defeats it:** keep the call, throw away its answer — `return view.stdout` →
`return ""`. An empty body has no `## Review` section either, so the gate still refuses, the
argv spy still sees the one `gh` call, and every assertion stays green. Any reader that
returns the gate's *failing* value (empty, `None`, a constant) passes a test whose expected
outcome IS that failing value.

**Why it looks covered:** the disagreement is real — swapping the precedence (reading the
marker first) does go red, because the marker's answer is the passing one. But a refusal is
also what a dropped, blanked or never-parsed answer produces, so the test cannot tell
"GitHub's body was read and lacked a review" from "nothing was read at all". Kin of
[[420|shape 420]] (a fail-closed fallback produces the end state the gate was meant to):
here the defeat is not a fallback but the authoritative read itself degrading to "no".

**Guard form that survives:** give the authoritative source the PASSING answer and the
losing source the failing one — GitHub returns a reviewed body, the stale marker an
unreviewed one — and assert the gate ACCEPTS. Now only a reader that actually carries
GitHub's text through can pass; skipping the call (marker wins) and blanking its answer both
go red.

**Found:** CMX-209 rework round 2, PR #745. `chela/reviewgate.py` `read_body()`; fixed in
`tests/test_reviewgate.py::test_an_open_pr_is_read_from_github_not_the_marker`. The same
round also pinned three unfed branches of the gate (a `### ` sub-heading directly under
`## Review`, a later non-empty `## Review` after an empty one, an unknown `_docs_only_diff`
result) and the reason cap ([[85b|shape 85b]]).
