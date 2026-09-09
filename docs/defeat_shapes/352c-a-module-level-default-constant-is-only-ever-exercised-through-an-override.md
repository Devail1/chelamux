## 352c. A module-level default constant is only ever exercised through an override, never at the real value it resolves to on import

**Assertion form:** a new reader takes an optional override parameter with a module-level
constant as its fallback — `tasklists.read_tasks(key, base=None)` resolves the root as
`(base or TASKS_DIR) / key`, where `TASKS_DIR = claude_config_dir() / "tasks"` is computed
once, at import time, from the real `$CLAUDE_CONFIG_DIR`/`~/.claude`. Every test in
`tests/test_tasklists.py` passes `base=tmp_path` explicitly (needed for filesystem
isolation), and every test in `tests/test_tasklists_dispatcher_api.py` reaches the same
function indirectly through the Flask route and monkeypatches `tasklists.TASKS_DIR` itself.
Both fixture styles are the *correct*, necessary way to isolate a filesystem-touching test —
but between them, nothing in the suite ever calls the reader with neither override in place,
so the constant's own resolved value — the thing a real, unconfigured host actually depends
on — is never the value driving any assertion.

**Mutation that defeats it:** repoint the constant itself, at its definition:

```diff
- TASKS_DIR = claude_config_dir() / "tasks"
+ TASKS_DIR = claude_config_dir() / "tasks-never-here"
```

Every existing test still passes: the `base=` tests never look at `TASKS_DIR` at all, and the
API tests overwrite it with their own `tmp_path` before the mutated line is ever read.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3820 passed, 0 failed, 0 error(s))
with the mutation in place — on a real, unconfigured host this change makes the feature find
*no* task list, ever, for any session.

**Why the two existing fixture styles both miss it:** an explicit `base=` parameter is the
right tool for isolating a filesystem test, and monkeypatching the *name* the module exposes
(`tasklists.TASKS_DIR`) is the right tool for isolating a caller that reaches the constant
indirectly — but "override the value under test" and "prove the value's own default is
correct" are different jobs, and every fixture in this PR does only the first. This is the
same shape [[319|shape 319]] and [[330|shape 330]] describe for a monkeypatched-away
*function* (its own body, and the argument its caller passes it, both go unexercised),
reproduced one level down for a monkeypatched-away module-level *constant*: nothing here
calls the real function against the real, un-overridden default.

**Guard form that survives:** exercise the constant's own resolution path with neither
override in place. Since the constant is computed once at import time from an environment
variable, this means setting `$CLAUDE_CONFIG_DIR` to a fake, isolated directory *before* the
module (re)computes it — `monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))` followed by
`importlib.reload(tasklists)` — writing a real fixture file under `<tmp_path>/tasks/<key>/`,
then calling `read_tasks(key)` with **no** `base=` argument at all. Reload the module again in
a `finally` block so the constant is restored to its real value for every test that runs
after — a reload's effect on a module-level global otherwise persists in `sys.modules` past
the end of the test that triggered it, cross-contaminating any test after it that (correctly)
assumes the default still points at the real config dir.

**Found:** CMX-352 rework round 3 (2026-09-09), PR #465. The judge's own throwaway-checkout
mutation battery found `TASKS_DIR = claude_config_dir() / "tasks"` → `... / "tasks-never-here"`
survived with 3820 tests green, having applied it to a checkout where every existing test
either passed `base=` or monkeypatched `TASKS_DIR` directly. Closed by
`test_tasks_dir_defaults_to_claude_config_dir_tasks_without_any_override` in
`tests/test_tasklists.py`.

**See also:** [[319|shape 319]] and [[330|shape 330]] — the same "override swallows the real
value's own correctness" mechanism, one level up (a monkeypatched function, not a
monkeypatched module constant).
