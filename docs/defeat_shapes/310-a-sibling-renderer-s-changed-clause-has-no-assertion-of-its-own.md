## 310. A sibling renderer's changed clause has no assertion of its own

**Assertion form:** a fix rewrites the same "how to recover" instruction in two
near-identical renderers of the same event — `chela doctor`'s `_installed_report` Finding
body and `chela plugin`'s `_report_installed_plugin` printout both render a "the INSTALLED
plugin is stale, here's how to fix it" message on the same drift-detected branch, as two
separate functions with two separate format strings. The PR updates the test for only one of
the two renderers. Worse, the assertions added there pin *unrelated* fields of the same
message — `"INSTALLED" in body`, `"PermissionRequest" in body`, `"STARTUP" in body` — every
one of which is equally true of the pre-fix and post-fix wording, because none of them sit
inside the clause the fix actually changed. The clause that changed (`Fix: ... chela update
...` vs the old `Fix: ... /plugin uninstall ...`) has no assertion pinned to it anywhere in
that test, and the doctor-side sibling test has no update at all.

**Mutation that defeats it:** revert either renderer's changed clause back to its pre-PR
wording verbatim, or swap one word inside the still-correct new wording:

```diff
-                + "\n    Fix: `chela update` already refreshes this copy for you, "
-                "non-interactively — no uninstall/reinstall needed (it runs `claude "
-                "plugin marketplace update <marketplace>` + `claude plugin update "
-                "chela@<marketplace>` for every confirmed-installed copy). Run it, or do "
-                "those two calls by hand. Hooks are read at agent STARTUP — a running "
-                "agent keeps the stale ones until it is restarted.",
+                + "\n    Fix: `chela plugin`, then in Claude Code `/plugin uninstall "
+                "chela@chela` + `/plugin install chela@chela` to refresh that copy. "
+                "Hooks are read at agent STARTUP — a running agent keeps the stale ones "
+                "until it is restarted.",
```

```diff
-        print("  (or by hand: `claude plugin marketplace update <marketplace>` then "
+        print("  (or by hand: `claude plugin marketplace refresh <marketplace>` then "
```

The first mutation is a full revert of the doctor-side clause back to the pre-PR "manual
uninstall/reinstall" wording. No test in `tests/test_installed_plugin.py` asserted anything
about that clause, so nothing noticed. The second mutation swaps one word (`update` →
`refresh`) inside the `chela plugin` by-hand fallback line. That renderer's message DOES have
a test, but the assertions there — `"chela update" in out"` and `"/plugin uninstall" not in
out` — are both still true after the swap: neither one names the by-hand fallback line at
all, only the two sentences around it. Both mutations left `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q` green (3218 passed, 0 failed).

**Why this slips through even though a test exists:** the test author reasoned at the level
of "does this message still say roughly the right thing" and pinned whatever substrings were
easiest to spot in the surrounding Finding body (severity words, an already-guarded path,
already-guarded field names) rather than the literal span the diff actually rewrote. A test
that looks like it exercises "the stale-install message" can pass on a message that says
something different in the one place it actually changed, if every assertion in it targets
prose the fix left untouched. And updating one of two sibling renderers' tests reads as
"the feature has a test" even though the second renderer — the one nobody touched — has
none.

**Guard form that survives:** when a fix touches two sibling renderers of what is
conceptually "the same" message, each renderer needs its OWN assertion on the literal clause
it changed — testing one is not evidence for the other, even when the two functions are a
few lines apart and render near-identical text. And within one renderer's test, assert the
exact new phrase the fix introduces (`"no uninstall/reinstall needed" in body`, `"claude
plugin marketplace update <marketplace>" in out`) together with the absence of the old
wording (`"/plugin uninstall" not in body`) — not fields merely adjacent to the changed
clause that were already true of both the old and new versions. Before trusting the
assertion, hand-revert the exact clause the fix claims to have changed and confirm the test
goes red.

**Found:** `chela/runtime_truth.py`'s `_installed_report` and `chela/main.py`'s
`_report_installed_plugin` (CMX-310, PR #386, rework round 1) —
`tests/test_installed_plugin.py::test_doctor_ERRORs_when_the_installed_manifest_disagrees`
had no assertion at all on the doctor Finding's `Fix:` clause, and
`test_chela_plugin_names_the_cache_path_when_the_install_is_stale` asserted `"chela update"
in out` / `"/plugin uninstall" not in out`, neither of which distinguishes `claude plugin
marketplace update` from `claude plugin marketplace refresh` in the by-hand fallback line.
Both reverts, applied by the judge to a throwaway checkout of the PR's head, stayed green
against `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3218 passed, 0 failed). Closed by adding
`"chela update" in body`, `"no uninstall/reinstall needed" in body`, and `"/plugin uninstall"
not in body` to the doctor test, and `"claude plugin marketplace update <marketplace>" in
out` to the `chela plugin` test.
