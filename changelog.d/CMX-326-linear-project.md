### Added

- **A Linear workflow can be scoped to one project of a team (`tracker.project`).** Every
  read and write it makes (the open set, the ID refresh, state writes, New task, size-gate
  splits) is limited to that project, and issues it creates go into it, so several repos
  can share one Linear team. Two dispatched workflows on one team must each set a
  different `project:`, or both refuse to claim work and `chela doctor` says why. The
  archive sweep stays team-wide, runs once per team, and keeps the largest `keep_done`
  set on the team. (CMX-326)
