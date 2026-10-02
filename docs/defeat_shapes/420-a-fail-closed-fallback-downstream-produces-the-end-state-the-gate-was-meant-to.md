## 420. A fail-closed fallback downstream produces the end state the gate was meant to

**Assertion form:** a switch decides whether to build a resource: "no outbox unless
`CHELA_PROXY_SESSION_DIR` is set". The negative test unsets the variable and asserts the
*end state*: `_Handler.outbox is None`. That reads like a test of the gate. But the code
after the gate already fails closed. If opening the resource raises, it sets the same
attribute to `None` ("no outbox — relay disabled"). So a mutant that removes the gate and
builds the resource anyway still ends in `None`, as long as the attempt happens to fail.

**Mutation that defeats it:** `if session_dir:` → `if True:`. Whether the mutant survives
depends on where the build lands. When the directory it is pointed at does not exist
(`Outbox(session_dir or "/nonexistent")`), `open()` raises, the fallback nulls the
attribute, and the negative test passes. Run it from the repo's cwd and `Outbox("")` writes
`./outbox.jsonl` into the checkout, which no assertion looks at.

**Guard form that survives:** make the ungated path's side effect *succeed and be seen*.
Run the entrypoint from a `tmp_path` cwd (`monkeypatch.chdir`), so an empty or relative
path would really create the file there. Then assert both the end state and the absence of
the side effect (`not tmp_path.rglob("outbox.jsonl")`, nor its status file). Test the
fallback separately, with a directory that genuinely cannot be opened. That way "None
because gated" and "None because the open failed" are two different tests, not one test
two routes can satisfy.

**Found:** CMX-420 rework round (2026-10-02), PR #572: the self-check's own first version
of the `chela/share_proxy.py::main` gate mutation survived this way. Closed by
`tests/test_sandbox_telegram_relay.py`'s `proxy_main` fixture
(`test_the_proxy_has_no_outbox_without_a_session_dir`,
`test_the_proxy_runs_without_an_outbox_when_its_dir_is_unwritable`).

**Related:** [407](407-an-environment-gated-branch-is-never-reached-because-no-fixture-has-the-environment.md)
is the judge's original survivor on the same function: no test drove `main()` at all.
[376](376-a-negative-property-proven-on-a-pure-resolver-is-not-proven-on-the-caller-that-appends-after-it.md)
covers the same round's `records_turn` survivors. The predicate was unit-tested, but the
`_forward` call site that applies it was not.
