### Fixed

- **A one-row client no longer leaves a window one row tall.** A tmux window keeps the size
  of its last client after that client detaches, so a minimized Wall tile could leave a session
  at 66x1, where no prompt can be read: a restart from the dashboard refused 10 of 13 sessions
  this way. chela now installs tmux hooks (when it creates its session, and when the dashboard
  starts) that put a window shrunk below 20 columns or 6 rows back at its last usable size and
  hold it there until a client big enough to size it comes back. `chela restart` gives an
  undersized window that no client is viewing a normal size (120x40) before it reads the prompt,
  and when a client still holds the window small it says "window is 1 row tall (66x1)" instead
  of only "could not be read". (CMX-503)
