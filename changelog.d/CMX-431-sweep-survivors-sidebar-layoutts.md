### Fixed

- **A custom sidebar group no longer jumps to another session after a tmux restart.** A
  `/clear` carries a window's group to its new session; a restart (or a reboot) hands the old
  window ids to other sessions, and the session resumed first into a reused id took the group
  of the one that had held it. The group now moves only while the window's Claude process is
  the same one. (CMX-431)
