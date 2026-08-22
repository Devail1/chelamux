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

---

### Also found on this task (round 2, same file): a guard clause's vacuous default is never independently exercised when every fixture shares the precondition that skips it

**Assertion form:** a reader function opens with an early guard clause —
`if <marker-of-the-uninitialized-state> is None: return <vacuous safe default>` — before its
main body, which scans structured content for the real answer. Every fixture in the test file
is built around the "normal," already-initialized case (a changelog with a dated release
section already present), so every single one of them takes the main body and none ever takes
the guard clause. This is close to [[57|shape 57]] ("a not-found arm is never independently
armed") but simpler and easier to miss precisely because it looks trivial: shape 57's else arm
needs a *paired* fixture (a key without its record) that a test author has to think to
construct; here the guard clause is a one-line early return that reads as too simple to need
its own test — "of course an empty/unset input returns the empty/vacuous default" — so nobody
writes the fixture that is nothing BUT that precondition (a changelog with only
`## [Unreleased]`, no dated section at all — the state of a repo before its first release).

**Mutation that defeats it:** replace the vacuous default with something that actively scans
the same input the main body would have scanned — `return set()` → `return {m.group("id") for
m in PATTERN.finditer(full_text)}`. Every existing fixture still takes the OTHER branch (the
`is not None` one), so this line never executes in the whole suite, and the mutation is
invisible. The danger is that the new behavior is not a random wrong answer: it accidentally
resembles what the function is *supposed* to do in the branch that DOES run (scan for
markers), so it will not be caught by a reviewer skimming the diff for a return statement that
looks wrong at a glance.

**Guard form that survives:** write a fixture whose input contains ONLY the state that trips
the guard clause and nothing else — no dated section, just `## [Unreleased]` — including
content that would produce a non-vacuous answer if the main body's logic ran on it by mistake
(a task-id marker sitting under `## [Unreleased]`). Assert the function returns the vacuous
default despite that marker being present, so a mutation that makes the guard clause scan the
input instead of returning the default is caught by the marker unexpectedly appearing in the
result — not just by an empty-input fixture, which a `return {}`-shaped mutation would also
satisfy vacuously.

**Found:** `chela/release_notes.py`'s `released_task_ids` (CMX-315 rework round 2, PR #393) —
`if dated is None: return set()` guards the case where a changelog has no dated release
section yet (a repo before its first release). Every existing fixture in
`tests/test_release_notes.py` built its changelog with a `## [0.8.0]` (or similar) dated
section, so the `is None` branch had never run once; the judge's mutation made it scan the
*whole* document instead (including `## [Unreleased]`), which would refuse a repo's
first-ever release with "already published in a dated release section" when no dated section
exists at all. Closed by `test_released_task_ids_treats_no_dated_section_as_nothing_released`
(and its mirror on `stale_fragments`,
`test_stale_fragments_accepts_everything_before_the_first_release`), both built from a
changelog that is nothing but `## [Unreleased]` plus a task marker, asserting the marker does
NOT come back as released/stale.
