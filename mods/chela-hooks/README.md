# chela-hooks-mod — SPIKE (CMX-157)

chela's merge gate and event posting as a Claude Code **Mod** (Claude Code ≥ 2.1.301),
built to run side by side with the shell-hook plugin in [`plugin/`](../../plugin) and
measured in [`docs/MODS_SPIKE.md`](../../docs/MODS_SPIKE.md). It is **not** installed by
`chela`, and it replaces nothing.

⛔ Load it in **one test session** only — never fleet-wide:

```bash
CHELA_WID=@test claude --plugin-dir /path/to/chelamux/mods/chela-hooks
```

| Variable | Default | Meaning |
|---|---|---|
| `CHELA_WID` | unset ⇒ nothing is posted | the window the events are filed under (the gate runs regardless) |
| `CHELA_MOD_HOOKS_URL` | `http://127.0.0.1:5001` | where `POST /hooks/<Event>` goes |
| `CHELA_MOD_MERGEGATE` | `<mod>/../../plugin/hooks/mergegate.py` | the gate script it runs |

Tests: `claude plugin validate mods/chela-hooks && claude plugin test mods/chela-hooks`
(also run by `tests/test_mods_spike.py` when a Mods-capable `claude` is on `PATH`).
