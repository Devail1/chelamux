## 56. A filesystem-failure fixture only reaches its target branch on some interpreter versions, and passes for a different reason on the rest

**Assertion form:** a guard's own bound clause (`except OSError:`) is closed with a fixture
that tries to *simulate* the failure at the filesystem level rather than injecting it
directly — here, a dangling symlink (`os.symlink(missing_target, dangling)`) standing in for
"glob finds the file, but `.stat()` on it raises." The test passes locally and the guard's
target line (`fresh_enough = False`) really is reached — on whichever interpreter wrote the
fixture.

**Mutation that defeats it:** none needed; the interpreter itself is the defeat. Whether
`os.path.realpath()` resolves a dangling symlink to a path at all — and so whether the glob
that feeds `.stat()` even returns a hit to stat — is not part of any language guarantee and
measurably differs between CPython 3.11 and 3.12's `pathlib`. On the version where the glob
comes back empty, the code takes an *earlier*, unrelated exit (`transcript_for_session`
returns `None`, so the "no `{sid}.jsonl` exists" branch fires) and the assertion on the
target branch's message (`"dead predecessor" in res.detail`) fails — CI red on 3.11, green
on 3.12, same commit, same test. The inverse failure mode is worse and easier to miss: on a
version where the fixture resolves differently, a test can pass while never touching the
`except OSError:` line at all, and nothing distinguishes that pass from a real one without
reading the interpreter's pathlib source.

**A related trap, found while closing this one:** the "obvious" fix — monkeypatch
`Path.stat` at the class level so the specific file's `.stat()` raises — reintroduces the
exact same version split it was meant to fix, for a different reason. 3.11's
`pathlib.Path.glob` calls `.stat()` on candidates internally while resolving them; 3.12's
does not. A class-wide `Path.stat` patch broad enough to make the freshness check's own
`.stat()` call raise *also* makes `transcript_for_session`'s glob raise on 3.11 (caught by
its own `except OSError: return None`, one frame further out than intended) — so the "fixed"
test still failed on 3.11, with the same symptom, for a second, distinct version-dependent
reason.

**Guard form that survives:** don't simulate the failure at the filesystem layer at all.
Write the file for real (so path resolution succeeds identically on every interpreter), then
monkeypatch the *specific function that returns the path* (`sessions.transcript_for_session`)
to hand back a thin proxy object around the real, already-resolved path — one whose `.stat()`
override raises OSError and whose every other attribute delegates to the real path. This
touches nothing upstream of the seam under test: glob, realpath, and the "found the file at
all" logic run unmodified and identically across interpreter versions; only the exact
`.stat()` call the guard's bound clause exists to catch ever fails. Also assert the resolver
got *past* the earlier exit it could wrongly take instead (here:
`"but no" not in res.detail`), so a fixture that silently stopped exercising the target
branch fails loudly rather than passing for the wrong reason.

**Found:** CMX-295 rework round 2 (2026-08-15), PR #368 — `test_a_pin_whose_transcript_cannot_be_STATD_is_refused_not_believed`
(closing shape #40 for `chela/sessions.py`'s pin freshness check) used a dangling symlink and
passed the judge's mutation battery locally, then reported CI red on `3.11` / green on `3.12`
for the identical commit. Reproduced on-machine with `uv run --python 3.11`; closed by
replacing the symlink fixture with the `transcript_for_session`-wrapping proxy described
above, verified green on both 3.11 and 3.12, and reverified the original mutation
(`except OSError: fresh_enough = True`) still turns it red on both.
