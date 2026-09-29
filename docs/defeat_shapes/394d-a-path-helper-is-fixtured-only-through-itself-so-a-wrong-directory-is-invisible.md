## 394d. A path helper is fixtured only through itself, so a wrong directory is invisible

**Assertion form:** a reader looks for an external program's file at a fixed layout
(`<config dir>/sessions/<pid>.json`, which Claude Code writes). The helper that names that
directory (`claude_sessions_dir()`) is also what every fixture calls to decide where to WRITE
the file. Writer and reader then agree by construction: whatever directory the helper returns,
the fixture puts the file there and the reader finds it. The suite never compares the layout
against the one the external program actually uses.

**Mutation that defeats it:** change the literal the helper returns (`/ "sessions"` →
`/ "session"`). Fixture and reader both move to the wrong directory together, so every test
stays green, and in production the reader never finds a real registry file.

**Guard form that survives:** fixtures build the external layout BY HAND from its documented
root (`Path(os.environ["CLAUDE_CONFIG_DIR"]) / "sessions"`), never through the helper under
test. Add one test that asserts the file sits at the hand-built path and that the reader
resolves it. A fixture that shares the code under test's spelling of a contract cannot test
that spelling.

**Related:** shape 394 (a validator fixtured only by the helper that writes honest records) is
the same self-agreement applied to a record's contents; this one applies it to its location.

**Found:** `chela/sessions.py` `claude_sessions_dir`, fixtures `tests/test_orchestrator_pin.py`
`_registry` and `tests/pin_scenarios.py` `_registry`, CMX-394 (PR #546, judge round 5).
