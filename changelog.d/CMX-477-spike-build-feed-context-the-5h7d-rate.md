### Added

- **`chela install-mod`: the 5h/7d limits, context and cost without a status line.** It installs
  the `chela-measure` mod, which is part of the package, so a PyPI install has it. The mod
  copies to `~/.claude/skills/` and does not edit `settings.json`. On every `session.measure`
  (after each turn, and whenever a limit moves a whole point) it writes the status line's own
  figures to `$CHELA_DIR/context/mod/<session>.json`, tagged `source: "mod"`. It draws nothing
  and shares no state with other mods. The context bar and the limit bars take whichever is
  fresher, this snapshot or the statusLine's. With neither, the bars read unknown, never 0%.
  `chela doctor` (`usage.sources`) names the producer feeding each session and each limit,
  and warns when the installed mod points at another `CHELA_DIR` or is out of date. The spike
  with the payload, firing rules and org-policy facts is in `docs/spikes/SESSION_MEASURE.md`.
  (CMX-477)
