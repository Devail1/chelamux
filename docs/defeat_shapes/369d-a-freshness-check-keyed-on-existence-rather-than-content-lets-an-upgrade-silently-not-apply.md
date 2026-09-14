## 369d. A freshness check keyed on EXISTENCE rather than CONTENT lets an upgrade silently not apply

**Assertion form:** a function that materializes a config file on disk skips the write
when "the file is already there," on the theory that a dispatcher polling every tick
shouldn't rewrite an unchanged file every launch. The idempotency test that proves this
starts from a `tmp_path` where the file does not exist, calls the function twice, and
asserts the second call is a no-op (same path, unchanged mtime). Every test in the file
shares that same starting condition — first call always writes, content is always
current — so nothing ever exercises the file existing with DIFFERENT content already on
disk.

**Mutation that defeats it:** narrow the skip condition from `path.read_text() == text`
(the file holds exactly this content) to `path.exists()` (the file is merely there,
whatever it holds). Every existing test still starts from a missing file, so the first
call in each test still writes, and the round-trip / idempotency assertions all still
pass. What breaks is invisible to all of them: in production the file persists in the
operator's `~/.claude` across chela upgrades, so a later change that narrows
`allowWrite`, adds a `credentials` entry, or flips a leaf ships a new `SANDBOX_SETTINGS`
that never reaches disk — the stale file from before the upgrade satisfies `path.exists()`
forever, and the dict-vs-literal test (docs/defeat_shapes/369) stays green the whole time
because it only reads the in-memory dict, never what got written.

**Why this is a family, not a one-off:** the same shape closed twice elsewhere in this
repo the same day — issue #514 (a daemon running pre-upgrade code in memory, so agents
wrote markers nothing read) and issue #515 (`ensure_schema` swallowing a permission error
as "column already exists," so the first migration after a boundary change goes live is
skipped). All three are "an upgrade that does not apply, and says nothing" reached by a
different route — the durable form of this entry is the class, not the settings file.

**Guard form that survives:** a negative control that is the mirror of the existing
idempotency (positive) test: write a file with deliberately STALE content to the target
path first, call the materializing function, and assert the on-disk content is now the
current value — not merely that the file exists. An idempotency test alone is only half
the property; without a stale-content counterpart, "skip when already there" and "skip
only when already correct" are indistinguishable to the suite.

**Found:** CMX-369 rework round 3 (2026-09-14), PR #518. The judge applied — to a
throwaway checkout of the PR head — narrowing `ensure_sandbox_settings_file`'s skip
condition from a content comparison to `path.exists()` in `chela/sandbox.py`;
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (4060 passed, 0 failed) because
every test in `tests/test_sandbox_boundary.py` started from a `tmp_path` where the file
didn't exist yet. Closed by
`test_ensure_sandbox_settings_file_refreshes_stale_content` (new), which pre-writes
mismatched content and asserts it gets overwritten with `sandbox.SANDBOX_SETTINGS` —
verified by re-applying the mutation by hand and confirming it goes red while the rest of
the file, including the positive idempotency test, stays green.
