## 66b. A shared row builder fills the optional field, so its absent and alternate-source branches are never reached

**Assertion form:** a test file's one row builder (`win(name, over)` in
`tests/sidebar_view.test.mjs`) gives every row a default `last_activity`, and never a
`recap_ts`. The sort test then asserted newest-first order over three such rows; the PR-badge
test rendered one PR in one state (`merged`). Every assertion was correct, and every one was
made over rows that carried exactly the fields the builder fills in.

**Mutation that defeats it:** three, all surviving the full suite (CMX-66 round 1):

1. `activityTs` reads `[_ts(a.last_activity), _ts(a.recap_ts)]` → `[_ts(a.last_activity)]`.
   No row ever had a `recap_ts`, so dropping the second source changed nothing.
2. The sort's "undated sinks to the bottom" comparator `(ta == null) - (tb == null)` flipped
   to `(tb == null) - (ta == null)`. No row was undated (the builder dated them all), so the
   null branch never ran.
3. The PR badge's `draft` arm was replaced with `false &&`. The only rendered PR was
   `merged`, which never reaches that arm.

**Guard form that survives:** list the branches the code takes on the field's *shape*. That
means absent, present from source A only, present from source B only, and both with B the
newer. Then build one row per branch, overriding the builder's default *explicitly* (for
example `last_activity: null`), and assert the observable result for each. For an
enumerated output (the badge's open / draft / merged / closed), render **every** member in
one fixture and read back each one's class and title. Also include the cases that sit next
to each other: a `draft` flag on a merged PR must stay `merged`. For a "sinks to the bottom"
rule, name the undated rows so that the name tie-breaker alone would put them **first**, and
run the fixture in both input orders.

**Found:** CMX-66 rework round 1 (2026-10-10), PR #636. Closed by
`Show PR status: the badge renders EACH state…`, `a row's last activity is the NEWEST of its
windows' last_activity AND recap_ts`, and `Sort by Last activity / Created: a row nothing
dates sinks to the BOTTOM…`. Each of the three mutations, applied by hand, now turns its
test red.
