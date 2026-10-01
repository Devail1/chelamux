## 402b. An env-override fixture that the API silently discards makes the mutant compute the same value as the original

**Assertion form:** the guard claims "X ignores `$TMPDIR`". It sets `TMPDIR` to a realistic but
made-up path, such as `/tmp/claude-1000/-home-someone-projects`, and asserts that the result is
the fixed default `/tmp/demo`.

**Mutation that defeats it:** actually read the variable, as in
`Path(tempfile.gettempdir()) / "demo"`. `tempfile.gettempdir()` accepts `$TMPDIR` only if that
directory exists and is writable. Otherwise it silently falls back to `/tmp`. With the made-up
path, the mutant also computes `/tmp/demo`. It agrees with the original, and the suite stays
green. The judge's "demo root is fixed /tmp/demo, never $TMPDIR" experiment on CMX-402 SURVIVED
this way.

**Why this is distinct from [[402|shape 402]] and [[73|shape 73]]:** the fixture value is
distinguishing on paper. The API under test throws it away before it can matter, because of a
validity rule the fixture does not meet. The override is a no-op, so the mutant and the original
are indistinguishable by construction.

**Guard form that survives:** make the override VALID for the API that consumes it. Here that
means a real, writable directory (`tmp_path / ...`). Then add a precondition assertion that the
override took effect (`assert Path(tempfile.gettempdir()) == scratch`) before asserting anything
that depends on it. The same applies to any env-driven API with a validity rule: `$HOME` for
`expanduser`, `$SHELL`, `$EDITOR` resolved on `PATH`, a config path that must exist, and so on.

**Found:** CMX-402 rework round 2 (2026-09-30), judge review of PR #553.
`tests/test_public_media.py::test_demo_fleet_root_is_fixed_and_ignores_tmpdir` guarded
`scripts/demo/fleet.py`'s `make_root`. Negative control: the judge's `gettempdir()/"demo"`
mutation now turns the test red.
