### Changed

- **The nightly sweep no longer pings the inbox on a night with no standard-tier merge.**
  That run is still logged, appended to the event log and recorded. Every other outcome
  still sends its notice, including the other skips (a fleet pause, the judge off, another
  sweep running). (CMX-371)

### Added

- **`chela doctor` flags a nightly sweep that has stopped running.** Every sweep run now
  records itself in `$CHELA_DIR/sweep-nightly/runs.json`. The new `sweep.nightly_last_run`
  check reads that file for each dispatched workflow with the sweep on. A last run more
  than 36 hours old is an error, so quiet nights cannot hide a dead scheduler. A workflow
  with no recorded run yet is a warning. (CMX-371)
