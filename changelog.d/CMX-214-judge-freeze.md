### Changed

- **`chela dispatch --pause --all` now freezes running judges too.** Each judge's process
  tree gets SIGSTOP, and `--resume` sends SIGCONT. Time spent frozen does not count against
  the judge's suite deadline or the daemon watchdog's wall. The watchdog leaves a frozen
  judge alone, and after a thaw its progress clock starts again from the thaw. A reaped judge
  tree that was stopped also gets SIGCONT, so it can act on its SIGTERM. `--hold-status`
  lists each suspended judge. (CMX-214 slice 3, CMX-251, #775)
