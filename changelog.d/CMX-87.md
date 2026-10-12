### Added

- **`chela release X.Y.Z --theme "…"` — the orchestrator prepares a release, the operator
  approves it with one tap, chela pushes.** In its own worktree off `origin/dev` (refused
  unless `origin/main` is an ancestor) it runs the release steps — the notes, the
  `pyproject.toml` bump, `uv lock` (which must re-record the new version), the release tests —
  then commits and tags LOCALLY and files an approval request. It pushes nothing; a failed or
  dirty step refuses with the reason and leaves nothing behind. The request is a Telegram card
  (version, theme, the dev commit, the task ids in the new CHANGELOG section, Approve / Deny),
  the dashboard's `/override/<id>`, or `chela merge-approve <id>`. Approving re-checks that
  `origin/dev` is still the commit the release was cut from and `origin/main` an ancestor,
  then pushes main, the tag and dev — fast-forward only, once — and the card ends on the
  released commit and, once the Release workflow built it, the GitHub Release's URL. A push to
  dev expires the request; deny, expiry or a failed check push nothing and remove the local
  tag. The approval stays human-only: the merge-gate hook (plugin 0.2.9) also denies a
  session's `python` call into `chela.release.complete`. See "Releasing" in CONTRIBUTING.md.
  (CMX-87)
