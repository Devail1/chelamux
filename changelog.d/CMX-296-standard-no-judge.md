### Changed

- **A `standard`-tier PR gets no judge round.** A PR that touches no core path (the
  dispatcher, the judge, the workflow loader, the sources, `chela/context.py`) and no
  always-human path is stamped `standard_no_judge` and is merge-ready on green CI +
  MERGEABLE, like `prose_only`. `chela merge` accepts it only while its live diff is still
  standard. Its `chela task-finished` runs `hand_in.lint_cmd` and one full suite, with no
  self-check battery and no sweep. Core PRs are unchanged, and the core set grows to cover
  the rest of the merge and containment machinery (`automerge`, `overrides`, `gateanswer`,
  `reviewgate`, `sizegate`, `basesync`, `sweep*`, `toolconfig`, `sandbox*`, `procguard`)
  and any `CLAUDE.md`/`AGENTS.md`, since those no longer meet a judge as standard. A workflow keeps the judge on
  every PR with `merge: {core: ["*"]}`. (CMX-296)
