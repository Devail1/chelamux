### Added

- **A one-minute demo of the merge gate.** An agent opens a PR, the judge corrupts its guard
  and the PR's test stays green, `chela merge` refuses, the agent adds a boundary test, the
  judge comes back clean and the merge goes through. The alternative ending is one Telegram
  tap on an override. The clip runs chela's real judge, merge gate and Telegram card against
  a throwaway repo, with only GitHub faked. It is in the README and on the landing page, and
  `scripts/demo/record-gate.sh` re-records it. (CMX-93)
