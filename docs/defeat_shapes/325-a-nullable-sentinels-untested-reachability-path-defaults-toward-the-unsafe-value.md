## 325. A nullable sentinel's untested reachability path defaults toward the unsafe value under mutation

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
to already need — and do not credit a branch as "tested" because a *different* except-arm
sharing the same handler happens to be exercised. For a `path.read_text()` →
`json.loads()` → `isinstance(data, dict)` chain that collapses onto one sentinel, that
means: file absent (`OSError`, already tested here), file present but not valid JSON
(`ValueError`, tested here only from round 4 on — see **Found**), and file present with
*parseable but wrong-shaped* JSON (the `isinstance` check, already tested here) — write a
real file for each and assert the sentinel, not the happy-path value. A single `except
(OSError, ValueError):` line is not one tested branch just because one of the two
exceptions it catches has a test; each exception, and each `isinstance` outcome, is a
distinct input shape a mutation can flip independently, and a test suite that narrows an
`except (A, B)` down to only `A` (or the reverse) is exactly this shape's mutation, just
applied by hand instead of by the judge.

**Found:** CMX-321 rework round 2, PR #409. The judge's required-mutation-set verdict
mutated `chela/hooks.py::registered_marketplaces`'s `else None` to `else set()` on the
`isinstance(data, dict)` branch; the existing tests (missing file, well-formed file) stayed
green because neither exercises "present but not a dict." Closed by adding
`test_registered_marketplaces_is_none_when_the_file_is_not_a_dict`, which writes `"[]"` to
`known_marketplaces.json` and asserts `registered_marketplaces() is None`.

**Found again, same function, the other half:** CMX-321 rework round 4, PR #409. This
file's own round-2 write-up (above) claimed the `ValueError` arm was "already tested here —
covered indirectly by `OSError, ValueError`," which was false: no test ever wrote
unparseable JSON to the registry, only a missing file (`OSError`) and a well-formed-but-
wrong-shape one (the `isinstance` branch). The judge narrowed `except (OSError, ValueError):`
to `except (OSError,):` — a plain `json.JSONDecodeError` (a `ValueError` subclass) then
propagates uncaught out of `registered_marketplaces` into `chela doctor`, `chela update`
and `chela plugin`, the three surfaces that call it — and every existing test, including
323's own, stayed green. Closed by adding
`test_registered_marketplaces_is_none_when_the_json_is_unparseable`, which writes the
literal string `"not json"` to `known_marketplaces.json` and asserts
`registered_marketplaces() is None`.
