## 402c. An inherited value whose fallback equals the expected constant passes both the sentinel sweep and the key-set allowlist

**Assertion form:** a from-scratch env builder is guarded by [[402|shape 402]]'s two checks. The
first overwrites every variable SET in `os.environ` with a `probe-<KEY>` sentinel and asserts that
no sentinel reaches the built env. The second asserts that the built env's KEY SET equals a
reviewed allowlist.

**Mutation that defeats it:** make an ALLOWED key read its value from the operator's env, falling
back to the constant it used to hold:
`"GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "demo@example.com")`. The key is already
on the allowlist, so the key-set check passes. The variable is unset on the test machine, so
the sweep planted no sentinel on it, the fallback fires, and the value equals the original.
Mutant and original agree. On the operator's machine the real email lands in the demo's
commits. The judge's round-3 experiment on CMX-402 SURVIVED this way.

**Why this is distinct from [[402|shape 402]] and [[402b|shape 402b]]:** shape 402's round-2 fix
catches a NEW key inherited while unset. Here the key is legitimate and only its VALUE is
inherited. The sweep's domain is "variables set here", but the leak's domain is "variables the
builder may read", and those two sets do not overlap on this machine. Shape 402b is an override
the API throws away. Here there is no override at all, because the fixture never set the
variable.

**Guard form that survives:** plant the sentinel on the union `set(os.environ) | ALLOWED_KEYS`, so
every key the builder emits is SET to a sentinel even if the test machine leaves it unset. Any
`os.environ.get(K, default)` for an emitted `K` then returns the sentinel. Also pin the
privacy-bearing values exactly (the git identity), so a fallback-to-constant cannot look correct.

**Found:** CMX-402 rework round 3 (2026-09-30), judge review of PR #553.
`tests/test_public_media.py::test_demo_fleet_env_is_temp_and_from_scratch`. The same round also
closed two catalogued shapes. The first was a helper tested alone while its only call site in
`up()` went unpinned ([[330]]): `write_shims` was dropped from `up()`. It is now closed by
executing the installed shims from `up()`'s own PATH. The second was a safety check proven on
one of its two consumers ([[07]]): the MARKER gate was tested on `make_root` but not on `down`.
It is now closed by a `down()` test with a foreign root plus a positive control. Negative
controls: all three judge mutations, blanking the pgrep shim's case arm, and moving `-L` into a
shim comment each turn the suite red.
