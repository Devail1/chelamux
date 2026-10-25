### Fixed

- **A prose-only PR's inbox notice no longer says "merged".** It fired when the verdict was
  stamped, while the PR was still open, so a pending PR read as done. It now says the PR is
  merge-ready on green CI with no judge round needed, and names the `chela merge` command that
  lands it. (CMX-272)
