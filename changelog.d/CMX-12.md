### Fixed

- **Sandboxed share sessions keep their conversations across relaunches, so `/resume`
  works.** The guest's HOME is a tmpfs, so every relaunch (a crash, an approved
  `chela-request mount`, `chela share-session <name>` again) threw its transcripts away. Each
  workspace now gets one owner-only host directory under `$CHELA_DIR/share-transcripts/`,
  bind-mounted read-write at `~/.claude/projects` in that workspace's guest only — nothing
  else of `~/.claude` is mounted. The launcher refuses to start if the directory can't be
  made or any part of its path is a symlink, `python -m chela.share_sandbox check` accepts
  exactly that mount and still fails on any other, and a guest's `chela-request mount` for it
  (or for `CHELA_DIR`) is still refused. Relaunch the share windows to pick it up — only after
  any live conversation in them is done, since it exists only in that window's tmpfs. (CMX-12)
