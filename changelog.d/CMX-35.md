### Changed

- **The dashboard sidebar groups sessions by project folder, like the Claude desktop app.**
  Each folder gets a quiet header with a `+` (a new session in that folder), a collapse
  chevron and a group menu (right-click, or the ⋯ on touch): New session, Move up / Move
  down, Collapse all / Expand all, and Archive all. Home-dir sessions sit in "Other" at the
  bottom, and two folders with the same name are told apart by their parent path. Archive
  all only hides finished rows from your sidebar (reversible via "Show archived"). It
  never kills a window, and never hides one that is working or waiting on you.
  Dispatched work is one row per run in a "Dispatched" group, `CMX-N · <title>`: the
  agent and judge windows fold together, and the row shows the run's state. The
  "Finished" cluster is gone: a done session stays in its folder. (CMX-35)
