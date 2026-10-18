### Added

- **`chela dispatch --pause --all` quiets the whole fleet.** It holds the queue, then
  presses Escape in each dispatched agent's window and tells it to start nothing until
  "resume". An agent inside `task-finished`, `judge sweep` or `judge self-check` gets the
  message without the Escape, so it finishes that command first. `--resume` sends "resume"
  to each agent it paused, and `--hold-status` lists them. Judges already running still
  finish. (CMX-214)
