### Documentation

- **`docs/TMUX_TOUCHPOINTS.md` lists every place chela types into or reads a tmux pane.**
  One row per call site, each with what it sends or reads, whether it arrives as a user turn
  or a native message with `from`, a bucket (agent message, launch and seed, state read, slash
  command, human input, display) and a keep / replace / remove recommendation. It records the
  Claude Code native features relied on, checked against the installed 2.1.330 binary, and
  writes out five follow-up slices, the first being to drop the silent tmux fallback for agent
  messages. `tests/test_tmux_touchpoints.py` parses `chela/` and fails on a pane-touching site
  with no row, so a new one can't land unlisted. (CMX-474)
