## 323. A nullable sentinel's untested reachability path defaults toward the unsafe value under mutation

**Assertion form:** a function's docstring commits to a two-value contract — "``None``,
never an empty set" — and a caller relies on that exact distinction (``None`` means
"cannot verify, don't guess"; an empty set would mean "verified: nothing is registered").
The function has more than one way to reach the documented `None` branch (file absent,
file present but unreadable/malformed), and the test suite covers some of those paths but
not all of them. The untested path is not a random omission: it is the one case where
"present but the wrong shape" (e.g. the file holds a JSON list or `null` instead of an
object) has to be classified by hand, rather than falling out of a try/except that already
has a test on each of its other branches.

**Mutation that defeats it:** flip the untested branch's return value from the documented
sentinel to the "wrong direction" one — `return set(data.keys()) if isinstance(data, dict)
else None` → `... else set()`. Every existing test still passes: "file missing" still
returns `None` (a different code path, `except (OSError, ValueError): return None`,
untouched), and "file present and well-formed" still returns the real key set (the
`isinstance` check still routes true). Only a registry that is present but not a dict
notices — and nothing exercises that shape, so the suite stays green while the function
now violates its own documented contract on that one input.

**Why the direction matters, not just the return value:** this is not an arbitrary
edge case. The caller (`marketplace_missing`) treats `None` as "cannot verify" and
refuses to flag anything; it treats a real (possibly empty) set as ground truth. Landing
on `set()` instead of `None` here means every installed plugin copy reads as
"marketplace gone" on any machine where this one registry file happens to be malformed —
a false ERROR from `doctor` and a false ⛔ from `chela update`, not a quiet no-op. A
sentinel bug in the *other* direction (returning a stale cached set instead of `None`)
would silently suppress a real warning instead — same shape, opposite harm, equally
untested by a suite that only ever exercises "missing" and "well-formed."

**Guard form that survives:** enumerate every way the function's own `try`/`isinstance`
guards can be reached, one test per branch, not just the branches an existing test happened
to already need. For a `path.read_text()` → `json.loads()` → `isinstance(data, dict)`
chain that collapses onto one sentinel, that means: file absent (already tested here),
file present with unparseable text (already tested here — covered indirectly by
`OSError, ValueError`), and file present with *parseable but wrong-shaped* JSON — write a
real file containing `"[]"` or `"null"` and assert the sentinel, not the happy-path value.
Do not assume a `try/except` and an `isinstance` guard share one failure mode just because
they return the same sentinel; each is a distinct input shape that a mutation can flip
independently.

**Found:** CMX-321 rework round 2, PR #409. The judge's required-mutation-set verdict
mutated `chela/hooks.py::registered_marketplaces`'s `else None` to `else set()` on the
`isinstance(data, dict)` branch; the existing tests (missing file, well-formed file) stayed
green because neither exercises "present but not a dict." Closed by adding
`test_registered_marketplaces_is_none_when_the_file_is_not_a_dict`, which writes `"[]"` to
`known_marketplaces.json` and asserts `registered_marketplaces() is None`.
