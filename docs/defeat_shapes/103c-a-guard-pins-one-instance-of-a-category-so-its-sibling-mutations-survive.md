## 103c. A guard pins ONE instance of a category — escaping, a clamped sign, an anonymous identity, a sort — and every sibling mutation of the same category survives

**Assertion form:** the suite asserts the happy value of a few fields (`/judge flaked/` is in
`.pa-why`; `age_s >= 0`; a spy sees `by == "dashboard"`; two rows come back) and calls the
category covered.

**Why that doesn't guard it:** each assertion is blind to the corruption of its own category:

- *escaping* — a plain-text reason renders identically escaped and raw; only a payload that
  would CREATE an element (`<img src=x onerror=…>`, `"><script>`, an attribute break-out) tells
  them apart, and it must be injected per field, not per sheet (one escaped field does not
  vouch for the next);
- *a clamped sign* — `max(0, now - created)` flipped to `max(0, created - now)` is a constant
  0, and `>= 0` holds for both; pin the value on a frozen clock (`90` ago -> `90.0`, a tick
  later -> `120.0`);
- *an identity-blind spy* — an unauthenticated request makes `by="dashboard"` (what a route
  hard-codes) and `by=_override_decider()` the same string; send the identity header on EVERY
  entry point and assert `dashboard:<who>`;
- *order* — random ids and two rows written in order cannot tell oldest-first from
  newest-first; set `created` out of write order and compare the whole list.

**Mutation that defeats it:** drop `escHtml(` around `r.reason`; flip the sign in `age_s`;
call `overrides.decide(..., by="dashboard")` from the confirm route; iterate `reversed(pending())`
(CMX-103 round 4). Suite green on each.

**Guard form that survives:** sweep the CATEGORY, not the instance — one parametrised test over
every field the surface prints with a payload that makes an element, a frozen clock with
literal expectations, the authenticated identity through each entry point, and out-of-order
fixtures — then corrupt each one and watch it go RED.
