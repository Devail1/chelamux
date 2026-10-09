## 38d. An "unknown" render is proven on the data path, while the error path that must reset to it is never driven

**Assertion form:** the Usage view's limit bars must read UNKNOWN, never a % and never 0%,
when there is no trustworthy reading. Two different paths lead there. On the **data path**,
`/api/usage` answers and a limit has `used_pct: null` (missing, stale or reset), and
`renderLimitBar` draws the unknown bar. On the **error path**, `/api/usage` fails (a rejected
fetch or a non-JSON 5xx), and the `catch` in `refreshUsage` must call `renderLimits(null)` to
repaint the bars. The JS suite mocked only a successful `/api/usage`. It asserted the unknown
bar from a payload carrying `used_pct: null`, which covers the data path only.

**Mutation that defeats it:** `renderLimits(null);` in the `catch` → `void 0;`. The bars are
no longer repainted on failure. Whatever the LAST successful refresh drew stays on screen,
so a 42% from ten minutes ago reads as current. The suite stayed green because no test ever
made `/api/usage` fail. Even a failure test that started from a fresh DOM would pass,
because the empty `#usage-limits` host contains no "%" to find. The defect is the
**previous** state surviving, so it only shows when a success comes before the failure.

**Guard form that survives:** drive the error path after a success, in one test. First
assert the precondition, that the good payload really painted a % (otherwise "no % after the
failure" proves nothing). Then make the next `/api/usage` fail through each failure channel
(rejected fetch, non-JSON 5xx, JSON `{error}`; see shape 6b). Assert that both bars exist, are
marked unknown, have no fill, and that no `\d%` appears anywhere in the limits host. Also
assert any other state the failure must drop. Here the cached payload must be cleared, so
switching windows cannot bring the stale rows back.

**Found:** CMX-38 rework round 3 (2026-10-09), PR #618. `chela/dashboard/static/js/usage.js`,
`tests/settings_usage.test.mjs`.
