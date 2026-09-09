## 351. A narrowed except-tuple (or swapped except-type) is only tested through a type a wider clause still catches

**Assertion form:** a fallback path is guarded by an except clause naming several exception
types (`except (OSError, UnicodeDecodeError, ValueError):`), or a single specific type
(`except re.error:`) whose docstring says explicitly what it exists for ("a present but
malformed/unreadable override... must never propagate"; "Raises ManifestError on a bad
schema/regex"). The test suite exercises the fallback, but every fixture that reaches it
raises a type *other* than the one a mutation would remove — a malformed-TOML fixture raises
`tomllib.TOMLDecodeError` (a `ValueError` subclass), never `OSError`; nothing constructs a
manifest with a syntactically invalid regex, so `_compile_all`'s `except re.error` is never
independently driven at all.

**Mutation that defeats it:** drop one member from the tuple (`except (OSError,
UnicodeDecodeError, ValueError)` → `except (UnicodeDecodeError, ValueError)`), or swap a
single named type for an unrelated one (`except re.error` → `except ZeroDivisionError`). Every
existing fixture still raises a type the (mutated) clause still catches, so the suite cannot
tell "this clause still handles `OSError`" from "this clause silently stopped handling it" —
until something actually raises the removed type, at which point it propagates uncaught
instead of degrading to the documented fallback.

**Why a passing "fallback works" test doesn't catch this:** a test proving the *fallback
behavior itself* (bundled manifest served, error logged) only proves the branch runs when fed
an exception type it happens to still catch. It says nothing about which types reach that
branch — the whole point of a multi-type except tuple is the types NOT exercised by the
existing fixture, and a bare "the fallback works" assertion is blind to exactly the dimension
the mutation attacks.

**Guard form that survives:** independently drive **each** named exception type through the
real code path that is supposed to raise it — not a fixture that happens to produce a
different member of the tuple. For an unreadable file (`OSError` from `read_text`), that means
actually forcing an I/O error on that specific path (`monkeypatch.setattr(Path, "read_text",
...)` raising `OSError`, scoped to the file under test — deterministic regardless of whether
the test runs as root, where a chmod-000 fixture would silently not reproduce the error at
all) and asserting the same loud-fallback contract as the `ValueError` case. For a swapped
single type (`re.error`), that means a fixture whose *regex itself* is syntactically invalid
(`top = ['(unclosed']`) — not merely a bad *schema* — asserted to raise `ManifestError` (not
propagate `re.error` uncaught, which is what the mutation actually does since `re.error` is
not a `ValueError` subclass).

**Found:** `chela/telegram/detection_manifest.py`'s `load()` except tuple and `_compile_all`'s
`except re.error` (CMX-351 rework round 2, PR #464). The judge applied both mutations in a
throwaway checkout; `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3783 passed) for
each, because `test_malformed_override_fails_loudly_and_falls_back_to_bundled` only ever
raised `tomllib.TOMLDecodeError` (`ValueError`) and `test_unreadable_override_directory_falls_back_to_bundled`
never reached the except block at all (`override.is_file()` is `False` for a directory, so
`load()` takes the "no override" branch, not the try/except). No fixture anywhere in the suite
raised a bare `OSError` or a genuine `re.error`. Closed by
`test_unreadable_override_file_falls_back_to_bundled` (an I/O error forced via `monkeypatch`
on `Path.read_text`, scoped to the override path only) and
`test_bad_regex_in_pattern_is_rejected_with_manifest_error` (an unparseable regex in a
`[[pattern]].top` list), each of which goes red under its respective mutation and green
without it.
