### Documentation

- **Agent standing rules: no shell-based writes.** A dispatched agent changes files only with
  the Edit or Write tool, never `sed -i`, `perl -pi`, `tee`, a `>` redirect or a heredoc into
  `python`/`cat`. Auto mode asks a human about those, so an unattended run stalled on the
  prompt for hours. The rule absorbs the existing "commit and PR messages via
  `git commit -F <file>`" line. (CMX-481)
