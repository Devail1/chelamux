## 38c. A fallback whose default target does not exist in the test environment looks the same as the empty setting

**Assertion form:** a setting distinguishes UNSET (fall back to a default) from EXPLICITLY
EMPTY (do nothing). `usage.extra_roots()` returns the default `/mnt/c/Users/*/.claude/projects`
glob only when the key is `None`; a saved `[]` or `""` means "scan no extra roots". The HTTP
test saved `[]`, fetched `/api/usage`, and asserted that the bot row from the previously saved
extra root was gone.

**Mutation that defeats it:** `if v is None:` → `if not v:`. A saved `[]` now falls back to the
default glob. Green, because the default glob matches nothing on the test host (no `/mnt/c`
under CI or in the judge's sandbox), so "fell back to the default" and "scanned nothing"
resolve to the same empty directory list and the same rows. The route's own response also
echoed `extra_roots()`, but the test never read it after the empty save. On a WSL machine the
mutation silently re-enables the Windows root that the operator just turned off.

Found in the same round, already catalogued: every `limits()` test passed `history=False`
while `report()` uses the default `True` (shape 312), and the HTTP test called
`usage._REPORT.clear()` between saving new roots and fetching again. That made the report
cache's roots key unobservable, because the cache was always empty when it was read (shape 421b).

**Guard form that survives:** point the default at a real fixture directory that holds a
recognizable row (`monkeypatch.setattr(usage, "DEFAULT_EXTRA_ROOTS", (str(tmp/"win"/"*"/"projects"),))`).
Then the unset → empty → unset sequence must show the row, then not show it, then show it
again. Also assert the returned value itself at each stored form (`None` → the default,
`[]` → `[]`, `""` → `[]`). Do not clear the cache between steps; the production client
never does.

**Found:** CMX-38 rework round 2 (2026-10-09), PR #618. `chela/usage.py`,
`tests/test_usage.py`.
