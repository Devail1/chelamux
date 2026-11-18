### Changed

- **A `risk:high` task is never merged without a judge round.** The merge tier used to come
  from the PR's file paths alone, so a `risk:high` trust-boundary change made in files that
  look like UI came out STANDARD: no judge, no sweep, auto-merge on green CI. The task's
  tracker risk level is now a floor: `risk:high` means at least CORE (the full judge round and
  hand-in); `risk:normal` and `risk:low` keep the file-based tier. The level is the one recorded
  on the run when the task was claimed. `chela merge`, the override question, `chela judge show`
  and the clean-verdict inbox notice say when the floor set the tier ("CORE (floor:
  risk:high)"). `chela/dashboard/identity.py` and `chela/share_requests.py` are now core paths
  too. (CMX-396)
