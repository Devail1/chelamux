## 315. A CLI refusal test pins exit code + stderr substring, both of which an uncaught crash also produces

**Assertion form:** a CLI's `except (SomeExpectedError,) as exc: print(f"error: {exc}",
file=sys.stderr); return 1` block is guarded by a test that runs the CLI end-to-end and
asserts `result.returncode == 1` plus a substring of `exc`'s message inside `result.stderr`
(e.g. the stale fragment's filename). That looks like it proves the CLI refused *cleanly* —
but a Python exception left uncaught also exits with a non-zero code (`1` for an unhandled
exception under `python -m`) and also prints its `repr()`/message text to stderr as part of
the traceback. If the exception text itself contains the same substring the test checks for
(here, the exception carries the stale fragment's filename either way — inside the caught
message or inside the traceback's own rendering of it), the two outcomes are
indistinguishable to an assertion that only checks "some substring appears somewhere in
stderr."

**Mutation that defeats it:** narrow the `except` clause so the specific exception the
refusal path exists for is no longer caught — `except (ReleaseNotFoundError,
StaleFragmentError) as exc:` → `except ReleaseNotFoundError as exc:`. The intended clean
refusal (`error: ...`, exit 1) is gone; what actually happens is an uncaught
`StaleFragmentError` propagating out of `main()`, printing a full Python traceback to
stderr and exiting 1 anyway. `result.returncode == 1` still holds (an uncaught exception
under `python -m` exits 1, same as the intentional `return 1`), and the fragment's filename
— embedded in the exception's own message — still appears somewhere in that traceback, so
`"CMX-309.md" in result.stderr` still holds too. Both assertions the test makes are
satisfied by the crash exactly as well as by the clean refusal, so the suite stays green
with the graceful-refusal code path deleted.

**Guard form that survives:** assert `"Traceback" not in result.stderr` (or equivalently,
that the ONLY line matching the expected `error: ...` prefix is present and nothing else
before it looks like a traceback header) alongside the existing returncode/substring checks.
This repo's own `test_cli_requires_version_unless_write_is_given` already carries this
assertion for exactly this reason — for the *other* refusal path in the same module — the
gap here was that the newer stale-fragment refusal test never had it applied to it too (a
narrower instance of [[311|shape 311]]'s "one sibling guarded, the structurally identical
one isn't," here between two error-handling paths in the same `main()` rather than between
two classes).

**Found:** `chela/release_notes.py`'s `main()` `--release` branch (CMX-315 rework round 1,
PR #393). `chela judge` narrowed the `except (ReleaseNotFoundError, StaleFragmentError)` to
`except ReleaseNotFoundError` in a throwaway checkout;
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3312 passed) with the corruption
in place because
`test_cli_release_refuses_a_stale_fragment_without_touching_anything` pinned only
`returncode == 1` and `"CMX-309.md" in result.stderr`. Closed by adding
`assert "Traceback" not in result.stderr` to that test.

**See also:** [[311|shape 311]] — the general form of "a negative/discriminating control
exists for one code path and was never mirrored onto a structurally identical sibling path";
this shape is that gap specifically between an intentional `except`-and-`return` refusal and
the uncaught-exception fallthrough it is meant to prevent.
