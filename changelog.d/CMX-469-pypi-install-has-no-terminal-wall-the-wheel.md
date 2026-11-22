### Fixed

- **A PyPI install has a terminal wall.** The wheel now ships the ttyd supervisor
  (`agent-terminals.sh`) and the `chela-env.sh` loader it sources, behind a new command,
  `chela agent-terminals` (`uvx chelamux agent-terminals` works too). Before, the wall's
  tiles stayed empty unless you fetched both scripts from the release tag. It warns when
  `ttyd` is not on PATH, and it never creates the tmux session: it waits for it
  (`CHELA_TERM_HEAL=false`; started directly, the script still self-heals). (CMX-469)
- **The terminal wall runs on macOS's stock `/bin/bash` 3.2.** The supervisor used bash 4
  associative arrays (`declare -A`) and failed on a Mac without Homebrew bash. A test now
  rejects bash-4-only syntax in it and runs it under a real bash 3.2 when the `bash:3.2`
  image is available. (CMX-469)
