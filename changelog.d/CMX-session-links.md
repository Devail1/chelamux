### Changed

- **Contributing rules now say, explicitly, that session links must not be published.**
  `CONTRIBUTING.md` and the dispatcher's agent prompt in `WORKFLOW.md` both spell out that a
  `Claude-Session: https://claude.ai/code/session_…` trailer belongs in no commit message, PR
  body, issue or changelog fragment in this repo: git history here is public and permanent,
  and the link is unopenable for anyone but its author. `Co-Authored-By:` stays — it
  attributes the work without publishing a session identity.
