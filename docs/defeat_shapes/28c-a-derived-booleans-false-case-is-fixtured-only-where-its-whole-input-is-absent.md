## 28c. A derived boolean's False case is fixtured only where its whole input is absent

**Assertion form:** a field is derived from a *part* of an optional record —
`"session_moved": bool(entry and entry["moved"])` in `/api/agents` (CMX-28). The tests pin
it True for a moved session, and pin it False for the control "no feed entry at all"
(`entry is None`). Alongside it, a rendered string falls back to a literal that the only
fixture also supplies (`p.get('session_kind') or 'background'`, fixtured with
`kind: "background"`), and a CLI flag is driven by calling `cmd_status(SimpleNamespace(
sessions=True))` directly.

**Mutation that defeats it:** drop the derived half — `bool(entry)`. With `entry=None` it
still reads False, with a moved entry still True; the one case where the two differ (an
entry exists but did NOT move — the pane's own claude is in the feed, the common case) is
never fixtured. Likewise `'background'` hardcoded agrees with the only kind fixtured, and
renaming `--sessions` to `--session-names` never reaches a test that builds `args` itself.
Suite green under each.

**Guard form that survives:** for a boolean computed as `A and B`, fixture the case where
`A` holds and `B` does not — that is the case that separates `A and B` from `A`. Give that
record every field the True case has (name, kind, sid, cwd) so no missing value can stand
in for the flag. For a `value or LITERAL` fallback, fixture a value that is NOT the literal,
plus one where it is absent. For a CLI flag, drive `main.main()` with a patched `sys.argv`
so the flag's spelling, its `dest` and the dispatch are all on the path under test.
