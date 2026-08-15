## 59. A "promote on success" guard is tested only with fixtures where naming and confirming happen together, so hoisting the write earlier — onto the NAMED-but-unconfirmed branch — survives

**Assertion form:** a resolver has two distinct facts about a candidate identity — a
lower-tier signal *names* it (an event log record, a `--resume` on a command line), and a
higher-tier check *confirms* it (the transcript the name points at actually exists on disk).
The code promotes the identity into a durable store only after confirmation:

```python
if sid:
    path = transcript_for_session(sid, base)
    if path is not None:
        _promote(wid, sid)
        return Resolution(wid, sid, path, "event_log", ...)
    tried.append(f"... names session {sid} ..., but no {sid}.jsonl exists ...")
```

documented as promoting a resolution only "the first time either tier succeeds." Every
promotion test in the suite constructs a fixture where the name and the confirmation agree —
the transcript for the named session always exists — because that is also what's needed to
exercise the success return value at all.

**Mutation that defeats it:** move the promotion call up one level, out of the `if path is
not None:` block and onto the bare `if sid:` — i.e., promote as soon as the lower tier names
something, before the higher tier has confirmed it:

```python
if sid:
    _promote(wid, sid)
    path = transcript_for_session(sid, base)
```

Every existing test still passes: in each one, `sid` names a session whose transcript really
exists, so the confirmation always succeeds anyway — the hoisted call fires on the exact same
input the original, correctly-placed call would have, and the return value, `res.path`, and
`res.source` are all unchanged. Nothing in the suite ever resolves a window where the name and
the confirmation *disagree* — a `sid` that is real but transcript-less — so no test can tell
"promoted because confirmed" apart from "promoted because merely named."

**Why this slips through even with the confirmed-success case covered:** a success-path test
proves the guard writes when it *should*. It does not prove the guard withholds the write when
it *shouldn't*, because in every such fixture the two conditions (named, confirmed) are
identical — collapsed into one boolean by construction, not by anything the code enforces. The
divergent case — the log names a session for which no transcript was ever written, the tier
that named it did **not** succeed, and the code falls through to the next tier via
`tried.append` — is exactly the population `_promote`'s own docstring excludes ("a real
identification actually made here... never the cwd guess, which is not an identification at
all"): an unconfirmed name is no more of an identification than a cwd guess is, and promoting
it durably pins a window to a session nobody ever actually saw running.

**Guard form that survives:** add a fixture where the named session has no matching
transcript at all — the event log (or the pane's command line) points at a `sid` that was
never written under the projects dir — resolve the window, and assert both that the
resolution fails (`res.path is None`) **and** that nothing was durably pinned
(`pins.session_id_for(wid) is None`). This is a distinct case from every existing promotion
test: it is the only fixture where "named" and "confirmed" produce different answers, so a
mutation that hoists the write onto the naming step alone goes red.

**Found:** `chela/sessions.py`'s `resolve_window` (CMX-296, PR #369, round 3) — the tier-1
call site's

```python
if sid:
    path = transcript_for_session(sid, base)
```

mutated to

```python
if sid:
    _promote(wid, sid)
    path = transcript_for_session(sid, base)
```

stayed green against `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3142 passed) because every
existing CMX-296 promotion test used a `sid` whose transcript was always written first. Closed
by `test_an_event_log_resolution_with_no_transcript_promotes_nothing`, which names a session
in the event log but never writes its transcript, and asserts the durable pin stays empty.

The identical shape was verified, by hand, to also survive at the tier-2 (`pane.resumed` /
`claude --resume <sid>`) call site a few lines below — `if pane and pane.resumed:` /
`_promote(wid, pane.resumed)` hoisted the same way — before
`test_a_cmdline_resolution_with_no_transcript_promotes_nothing` was added to close it too;
the two call sites share the shape and needed one test each.
