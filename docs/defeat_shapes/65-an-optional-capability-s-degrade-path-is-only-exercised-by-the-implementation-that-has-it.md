## 65. An optional capability's degrade path is only exercised by the implementation that HAS the capability

**Assertion form:** several interchangeable (duck-typed) implementations sit behind one call
site. Only SOME of them implement an optional method; the caller probes for it defensively —
`getattr(obj, "method", None)` / `hasattr` / a try/except around the call — and degrades
gracefully (empty list, `None`, a skipped branch) when it's absent. The test suite drives the
call site, but every fixture happens to construct the implementation that DOES have the
method, so the "it's absent" branch of the probe is never the one actually reached — the
suite proves the happy path works and says nothing about the degrade path.

**Mutation that defeats it:** replace the defensive probe with a direct attribute access —
`getattr(source, "list_parked_tasks", None)` → `source.list_parked_tasks`. Every existing
fixture still uses the capable implementation, so `source.list_parked_tasks` resolves fine and
the suite stays green. The mutation is only observable through the implementation that lacks
the method, which nothing in the suite ever constructs at that call site — and there the
direct access raises, and if that call site is wrapped in a broad `except Exception`, the
error doesn't even surface as a crash: it silently flips a whole entry to an error state.

**Guard form that survives:** for a duck-typed interface, count the DISTINCT implementations
behind the call site — not the call sites, the *implementations* — and drive the exact call
site with each one, at least once with an implementation that deliberately lacks the optional
method. A fixture built only from the capable implementation, however thorough its coverage of
that implementation's own behaviour, never exercises the `None`/absent branch of the probe.

**Found:** CMX-298 rework round 4 (2026-08-16), PR #372 — `chela/dashboard/app.py`'s
`/api/dispatcher` reads `list_parked_tasks = getattr(source, "list_parked_tasks", None)`
because — per its own comment — "Only the markdown source has a notion of this; gh_issues has
no bullet-level marker to read." `GhIssuesSource` genuinely defines no `list_parked_tasks`.
Every test that drove `/api/dispatcher` end-to-end used a `markdown` tracker (which has the
method); `tests/test_gh_issues_allowlist.py` exercises `GhIssuesSource` directly but never
through this endpoint. Closed by
`tests/test_dispatcher_discovery.py::test_api_dispatcher_survives_a_gh_issues_workflow_with_no_list_parked_tasks`,
which configures a real `gh_issues` tracker (stubbing `gh` itself) and asserts the resulting
workflow entry carries no error.

**Related:** [63](63-a-guard-fixed-on-one-branch-hop-of-an-invariant.md) is the closest
neighbour — both are "a fixture exercises one of several equivalent paths and calls it done" —
but 63 is about the SAME value crossing several branches/hops of ONE implementation; this shape
is about ONE call site backed by several INTERCHANGEABLE implementations, where the fixture
space quietly narrows to only the one(s) that happen to support the optional path.
