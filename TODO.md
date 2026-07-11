# TODO

## Open

- [x] Dashboard: auto-discover dispatcher workflows so dogfood dispatch runs appear in the Dispatcher/Kanban views WITHOUT manually setting `CHELA_DISPATCH_WORKFLOWS`. Today `chela/dashboard/app.py`'s `/api/dispatcher` iterates only the env-configured `DISPATCH_WORKFLOWS` (returns `{configured:false, workflows:[]}` otherwise), even though runs exist in the dispatch runs DB. Change the Dispatcher + Kanban data so it ALSO surfaces every workflow that has runs recorded in the runs DB (grouped by `project_key`) and/or auto-discovers a `WORKFLOW.md` at the repo root — so runs show regardless of which tmux session the agents ran in (runs are session-independent). Keep the existing `CHELA_DISPATCH_WORKFLOWS` explicit config working (union, don't replace it). Add/adjust tests; keep `uv run ruff check chela tests` green.

## Done

- [x] Add a `telegram-setup` skill (PR #14).
- [x] Scaffold the `chela/telegram/` package + `[telegram]` extra (PR #15).
