### Added

- **`chela task show`: a dispatched agent reads its own Linear issue.** It prints the issue's
  current title, description and comments (oldest first, author and timestamp), so a comment
  on the issue now steers the next attempt. Only comments by the API key's own user and the
  workflow's `linear.comment_authors` are shown; bot and integration comments are dropped and
  counted. A full page says TRUNCATED, and a failed read says "could not fetch" and exits
  non-zero. It is read-only, and the key stays in chela's process, never in the agent's
  environment. A Linear workflow's dispatch and rework prompts tell the agent to run it at the
  start of every attempt. (CMX-287)
