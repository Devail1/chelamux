<!-- Target `dev`, not `main`. main is the promotion branch. -->

## What & why

<!-- What does this change, and what problem does it solve? Link any related issue. -->

## Checklist

- [ ] Branched from and targeting **`dev`** (not `main`)
- [ ] `uv run ruff check chela tests` passes
- [ ] `uv run pytest -q` passes
- [ ] **Guards go RED under corruption** — new/changed guards fail when the invariant they protect is broken (the judge enforces this)
- [ ] **A `changelog.d/CMX-<task-id>.md` fragment** added for any user-facing change
      (never edit `CHANGELOG.md` directly — see `changelog.d/README.md`)
- [ ] Docs updated if a `CHELA_*` knob, command, or hook changed

## Notes for the reviewer

<!-- Anything the reviewer (or the adversarial judge) should know: manual verification steps, trade-offs, follow-ups. -->
