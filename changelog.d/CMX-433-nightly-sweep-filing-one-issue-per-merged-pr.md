### Changed

- **The nightly sweep files one issue per merged PR, not one per survivor category.** It
  groups survivors by the PR their lines blame to, or by file when the PR is unknown. Each
  issue lists every mutation: category, `file:line`, before → after, and same-shape sites.
  The PR is in the title. A night files at most `sweep.max_issues` new issues (default 5).
  Any other groups are folded into the largest issues, never dropped, and the inbox notice
  says how many survivors went into each issue. New survivors for a PR whose issue is still
  open are added to it as a comment. On 2026-11-18 the old filer opened 29 issues for 4
  PRs. (CMX-433)
