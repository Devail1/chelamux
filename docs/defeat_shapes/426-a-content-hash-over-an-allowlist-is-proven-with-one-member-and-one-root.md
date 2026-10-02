## 426. A content hash over an allowlist is proven with one member and one root

**Assertion form:** a version string is a hash over every file under a set of ROOTS whose
suffix is in an ALLOWLIST — CMX-426's `compute_asset_version(roots=(static, templates))`
over `{".js", ".mjs", ".css", ".html", ".svg"}`, so that any deploy which changes what the
browser caches moves the version and the open page offers a reload. The guard writes ONE
`.js` file into ONE temp root, edits it, and asserts the version moved. A second test asserts
`compute_asset_version() == ASSET_VERSION` — the default roots compared with themselves.

**Mutation that defeats it:** shrink either set. Drop `".css"` from the allowlist, or drop
`_TEMPLATES_DIR` from the default roots: the `.js` fixture still moves the version, and the
default-roots call still equals the module constant, because the constant was computed by
the same mutated default. In production a CSS-only or template-only deploy now leaves the
version unchanged, so every open page keeps its stale stylesheet with no banner — the exact
bug the feature exists to fix. Renaming a file (dropping the path from the hash) slips
through the same way: the fixture only ever edits contents.

**Guard form that survives:** parametrize the "edit moves the version" test over EVERY
allowlisted suffix, add a negative control (a non-allowlisted file does NOT move it), a
rename case, and pin the default roots against an INDEPENDENT reconstruction — copy the real
`static/` and `templates/` trees into `tmp_path`, hash them explicitly, assert equality with
the running constant, then edit the copied template and assert it moves. Comparing a default
with itself proves nothing about what the default is.

**Found:** CMX-426 rework round 1 (2026-10-01), PR #578 — the judge reported one held-out
survivor without naming it; auditing every guard for it found both mutations above green
under `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`. Closed in `test_version_changes_when_a_served_asset_changes`
(parametrized), `test_version_ignores_files_the_browser_never_loads`,
`test_version_changes_when_a_served_asset_is_renamed` and
`test_running_version_covers_both_static_and_templates`.
