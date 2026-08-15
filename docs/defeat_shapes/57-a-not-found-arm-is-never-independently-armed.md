## 57. A resolver's "not found" arm is never independently armed, because every fixture that sets up the positive case also creates the thing it looks for

**Assertion form:** a multi-tier resolver checks a lookup's result with `if found is not
None: ... else: <refuse, recording why>`, and every test that exercises this tier does so
by arranging BOTH halves together — it sets up the key (a pin, a token, a cache entry) AND
the record that key resolves to (a transcript file, a row, a cached value) in the same
fixture, because that is the natural way to write "prove the tier resolves." No fixture
ever arms the key alone. The suite is green, and the tier reads as covered because every
test that touches it takes the `if` branch — but the `else` arm has never once executed,
so nothing has ever proven what happens when the key is real but the thing it names isn't
(yet, or ever).

**Mutation that defeats it:** merge the two arms so the `else` is unreachable — e.g.
`if found is not None:` → `if found is not None or True:`. Every existing fixture still
supplies a non-None `found`, so the branch taken is identical for all of them and the
suite stays green. What actually changes is invisible to every test: on the one input this
tier is SUPPOSED to refuse (a key with no matching record), the code now proceeds to use
`found` as if it were real. If anything downstream calls a method on it (e.g. `.stat()` on
what should have been a real path), the crash is a bare, uncaught exception of the WRONG
type for any existing `except` clause nearby — not the graceful refusal the `else` arm was
there to produce.

**Guard form that survives:** add a fixture that arms the key WITHOUT creating the record
it names — a pin naming a session whose transcript was never written, a token with no
matching row — and assert the resolver takes the refusal path: the negative result AND the
specific "not found" detail/reason the `else` arm records (not just "some source of
detail," since a resolver with several failure arms can produce a truthy-looking string
from the wrong one and still pass a loose assertion). This is not an exotic state to test:
often it's the ORDINARY one — e.g. a freshly spawned resource whose key is written before
the record it names has been created at all — so the fixture is realistic, not contrived.

**Found:** CMX-295 rework round 3 (2026-08-15), PR #368 — `chela/sessions.py`'s
`resolve_window` pin tier (CMX-295) does `path = transcript_for_session(pinned, base);
if path is not None: <freshness check> else: tried.append(...)`. Every existing pin test
(`_pin(monkeypatch, {...})`) paired the pin with a `_transcript(...)` call creating that
exact session's transcript, so the `else` arm — reached when `spawn_window` writes the pin
before the session's first transcript byte exists, i.e. on every freshly spawned window —
had never run. The judge folded the `if` into `if path is not None or True:` in a
throwaway checkout; the full suite (3140 tests) stayed green because `path` was never
`None` in any fixture. Closed by a new test that pins a session with no transcript at all
and asserts the refusal detail names the missing `.jsonl` — which the mutation defeats for
real, since `None.stat()` raises `AttributeError`, uncaught by the freshness check's
`except OSError`, and blows the test up instead of producing a graceful refusal.
