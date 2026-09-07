## 347. A new branch copied from a sibling's return statement leaves its differing literal field and its never-hit `is None` fallback both unpinned

**Assertion form:** a new error-result branch is added by copying the shape of an existing
sibling branch elsewhere in the same function — same `ApplyResult(ok=False, step=..., error=...)`
constructor, same `sync_cp is None` guard around it. The new branch's test drives it down the
one path the branch exists for (`uv sync` returns a non-`None`, non-zero result) and asserts
the fields that carry the branch's *point* — `result.ok`, `result.step`, the error substring.
Two things never get an assertion of their own:

1. A literal field on the same constructor call that differs from its sibling's — the sibling
   branch (a few lines below, on the pull path) writes `behind_before=status.behind`; this new
   branch, running earlier where `status.behind` is guaranteed `0`, writes the literal
   `behind_before=0`. The test never reads `result.behind_before` back, so nothing pins that
   this branch's constructor call actually carries the value its position in the function
   requires.
2. The `sync_cp is None` half of `err = sync_cp.stderr.strip() if sync_cp is not None else
   "uv sync failed to run"` — no fixture ever makes `_sh` return `None` for this call site, so
   the literal fallback string has never been the value actually returned.

**Mutation that defeats it:**

```diff
-                return ApplyResult(ok=False, step="uv-sync", behind_before=0, error=err)
+                return ApplyResult(ok=False, step="uv-sync", behind_before=7, error=err)
```
```diff
-                err = sync_cp.stderr.strip() if sync_cp is not None else "uv sync failed to run"
+                err = sync_cp.stderr.strip() if sync_cp is not None else ""
```

Both mutations changed the file, it still parsed, and `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
stayed green (3752 passed) under either one — the first because no assertion ever reads
`result.behind_before` on this branch; the second because no fixture ever drives `_sh` to
return `None` at this call site, so `err` is always computed from a real (non-`None`)
`sync_cp` in every existing test.

**Why one branch produced two independent gaps:** copying a sibling's return-statement shape
copies its *literal* differences along with its structure — a value that's correct only
because of where the new branch sits in the function (`behind_before=0` is right here and
would be wrong on the pull path) reads as "the same pattern as the sibling" and doesn't
prompt a fresh assertion the way a hand-written value would. And the `is not None else
<literal>` half of that same line is a defensive fallback for an input class (`_sh` returning
`None` on a missing binary or a timeout) that every fixture in the file happens not to
construct — the same blindness as [[40|shape 40]], recurring at a different call site.

**Guard form that survives:** when a new branch is built by copying a sibling's return
statement, diff the two constructor calls field-by-field and assert on every field that
differs, not just the ones the new branch's docstring/PR description calls out — a differing
literal on an otherwise-identical call is exactly the kind of value nobody hand-writes a
fresh assertion for. Separately, for any `x if cond is not None else "<literal>"` fallback,
add a fixture that actually makes `cond` `None` (here: `_sh` returning `None`) and assert the
literal fallback string, not just the non-`None` branch's computed value — the same lesson as
[[40|shape 40]], applied to a fallback string rather than a coercion.

**Why this is distinct from [[41|shape 41]]:** 41 is a *rendered* sibling cell in a table,
unasserted while cells around it in the same row are pinned. This is a *constructor-argument*
sibling on a dataclass return statement — the "row" is a single Python call, not a DOM table,
and the fix is a plain field assertion (`result.behind_before == 0`) rather than reading a
specific cell/selector back.

**Found:** `chela/update.py`'s stale-only restart path (CMX-347 rework round 1, PR #454) —
judge mutations `behind_before=0 → behind_before=7` and the `is not None else "uv sync failed
to run" → is not None else ""` fallback, both surviving the suite green (3752 passed). Closed
by adding `assert result.behind_before == 0` to
`test_apply_syncs_deps_before_restarting_a_stale_only_service` and a new test,
`test_apply_reports_uv_sync_missing_binary_on_the_stale_only_path`, that makes `_sh` return
`None` for the `uv sync` call and asserts `result.error == "uv sync failed to run"`.
