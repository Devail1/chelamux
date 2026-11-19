### Changed

- **A 🧊 STUCK notice now names a process blocked reading its stdin.** When an agent goes
  quiet, chela looks in `/proc` for a leaf process in the agent's worktree that is asleep
  reading a pipe or terminal stdin (for example a `cat` left behind by a broken heredoc),
  has existed at least `CHELA_STDIN_BLOCKED_ALERT_S` seconds (default 300), and started
  no earlier than the agent's last activity (so a long-lived stdio server is not named). The notice
  then says which one: "blocked on stdin: cat PID N, under background shell bash PID M,
  started HH:MM. Killing it frees the agent without losing work." A busy process, such as
  pytest writing output, is not named. (CMX-443)
- The agent standing rules now say to write commit and PR messages to a file and pass it
  with `git commit -F <file>` / `--body-file`, never a heredoc piped into `cat`. (CMX-443)
