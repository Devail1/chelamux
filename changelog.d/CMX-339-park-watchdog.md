### Fixed

- **A parked run is no longer re-nudged and then failed by the idle watchdog.** `chela park`
  kept a run out of the inbox's STUCK notice, but the dispatcher's idle-at-empty-prompt
  watchdog never checked it: an agent parked and told to wait was re-sent its task prompt,
  then failed "idle at empty prompt after re-nudge" about an hour later. The watchdog now
  stands down for a parked run (a manual park, or the automatic park of a suite-slot wait)
  and restarts its idle clock on every parked tick, so after `chela unpark`, a reached
  `--until` or the park max, the agent gets the full idle budget again. (CMX-339)
