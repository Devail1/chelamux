## 103. A first-call branch on module-level state is primed by an EARLIER test in the same file — the test named for the first call never sees it

**Assertion form:** a module keeps a latch at module scope (`let _seen = null;` — "null until
the first listing primes it") and branches on it (`const fresh = _seen === null ? [] :
rows.filter(r => !_seen.has(r.id))`). The test for "a reload never re-announces what was
already waiting" ticks once with notifications OFF ("primes"), turns them on, ticks again, and
asserts only the NEW request notified. It passes, and its comment says it covers the priming.

**Why that doesn't guard the first-call branch:** an ES module (like a Python module) is
evaluated ONCE per process and cached; every test in the file shares the one instance. By the
time this test runs, the file's first test has already ticked, so `_seen` is a Set and the
`=== null` arm is never taken again — in this test or any other. The test's own "priming"
tick is not a first call at all; its notifications were off, so it would have announced
nothing either way. Replace the branch with `false ? [] :` and the real first call on a page
load would notify for every request already waiting — and the suite stays green, because the
one tick that could tell the difference happened in a different test, with notifications off.

**Mutation that defeats it:** `const fresh = _seen === null ? [] :` → `const fresh = false ?
[] :` (CMX-103, `chela/dashboard/static/js/approvals.js`). Suite green.

**Guard form that survives:** give the test its OWN module instance whose latch really is
unprimed — in node, re-import with a query string (`import('…/approvals.js?first-listing')`
evaluates a fresh copy); in Python, `importlib.reload` or reset the global in a fixture — and
make the first call the one that would misbehave: opt IN first, then tick with requests
already waiting, assert nothing fired; then tick with a new one and assert exactly it fired.
A guard on a "first time only" rule must start from a state the rest of the file cannot have
touched.

**Siblings in the same verdict:**

- the refresh poll's `await tickApprovals();` had no test loading the real entry point
  (`main.js`), so dropping the call left the pill dead on every tab while the module's own
  suite stayed green — shape [[358]]'s wiring half; the fix loads `main.js` in jsdom and
  asserts the pill appears from the page's own first refresh, plus the SSE listener, the
  shipped pill markup's `onclick`, and the Settings select's `onchange` (inline handlers need
  jsdom's `runScripts: 'dangerously'` to compile at all — without it they are silently inert).
- a forward-compat hook (`kind != "override" → not blocked`) for a producer that had not
  landed yet (CMX-87's release requests) had no fixture of that kind, so the branch was dead in
  the suite; the fix files a `kind: "release"` record and asserts it is listed approvable and
  never looked up as a run, next to an override whose run is gone ("the run no longer exists").
