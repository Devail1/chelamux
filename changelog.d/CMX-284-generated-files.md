### Changed

- **The sweep and the judge no longer mutate generated files.** One shared list
  (`chela/generated.py`: the committed `chela/dashboard/static/app/**` bundle, `uv.lock`,
  `chela/dashboard/frontend/pnpm-lock.yaml`, `**/*.min.js`) is excluded from `chela judge
  sweep`'s mutation candidates and from the judge's experiments, and the hand-in size gate
  never counts those files. Each round says how many it skipped. A React port slice used to
  spend almost all of its sweep budget on minified JS that no test can guard; CI's
  "committed build is fresh" step is that bundle's guard. (CMX-284)
