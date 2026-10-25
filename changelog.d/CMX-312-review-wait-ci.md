### Fixed

- **A PR waiting on CI no longer gets a "no judge round has started" notice.** No judge starts
  before CI is green, and a standard or prose PR never gets a judge at all, yet a run that sat
  20 minutes on a slow or re-running CI job was told "NO judge round has started … is the
  queue held or the daemon down? Or review it yourself". The review-wait notice now names CI
  as the wait (still running, re-running a flake, red, or unreadable). A standard or prose PR
  is never "queued for the judge", and if one is green with no verdict the notice asks about
  its no-judge verdict, not a judge. (CMX-312)
