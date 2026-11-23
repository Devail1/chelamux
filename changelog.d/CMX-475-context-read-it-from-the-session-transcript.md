### Fixed

- **The context % no longer needs the installed status line, and is right after `/compact`.**
  It now comes from the session transcript: the last assistant turn's input, cache-read and
  cache-creation tokens, which is the figure Claude Code's own `/context` prints. That covers
  sessions started before `chela install-statusline`, org-managed settings that override the
  status line, and users with a status line of their own. Where the status line is installed
  and has the newer reading, chela uses it. After a `/compact`, the bar reads "compacted"
  instead of the stale pre-compact number until the next turn. A session with no turn yet
  (including one `/clear` just started) reads "—", never 0%. The window size (1M or 200k)
  comes from the first source that knows it: a `[1m]` model in the transcript, the launch
  `--model` or `ANTHROPIC_MODEL`, Claude's `model` setting, the status line, or usage past
  200k. When none knows it, the bar shows tokens ("430K") with no %, and never guesses
  200k. `chela doctor` names each live session's source and flags a session with none as
  unknown. (CMX-475)
