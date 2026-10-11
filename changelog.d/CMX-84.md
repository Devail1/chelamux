### Added

- **A `claude` you type by hand into a chela window gets Remote Control too.** While the
  Remote Control setting is on, every window chela opens for a human ("+ New shell", the
  dashboard launcher, Telegram `/new`, a resume) defines a `claude` shell function, so a
  hand-typed `claude …` starts with `--remote-control <window name>` and shows up in the
  Claude desktop app under the window's current name. `-p`/`--print`, help/version, a
  prompt or subcommand, or an explicit `--remote-control` are passed through unchanged.
  Dispatched agents and judges never get it. Turning the setting off affects windows opened
  afterwards; an already-open shell keeps its function until it exits. (CMX-84, #646)
