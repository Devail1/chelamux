### Changed

- **A clean verdict survives a merge from the base branch.** When a judged PR's head moves
  and the move touches none of the PR's own files, the dispatcher keeps the clean verdict,
  re-stamps it on the new head with the reason `carried: base-only merge`, and spawns no
  judge; `chela merge` accepts it. When a PR file did change, the re-judge re-runs the
  previous round's experiments first and the judge is asked for new ones only on the lines
  changed since, instead of drawing a fresh sample of lines already judged. (CMX-289)
