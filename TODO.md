# TODO

## Open

- [x] Scaffold the `chela/telegram/` package and a `chelamux[telegram]` optional extra (fold step 1, structure only — NO bridge logic yet): create `chela/telegram/__init__.py` with a module docstring noting the Telegram bridge is adapted from six-ddc/ccbot (MIT), add a top-level `NOTICE` file carrying the six-ddc MIT copyright/attribution, and add a `[telegram]` optional-dependency group to `pyproject.toml` pinning `python-telegram-bot`. Keep `uv run ruff check chela tests` green.

## Done

- [x] Add a `telegram-setup` skill (shipped as PR #14).
