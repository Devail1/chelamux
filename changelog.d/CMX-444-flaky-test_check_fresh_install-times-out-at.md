### Changed

- **The clean-container install check left the unit suite.** `pytest -q` no longer builds a
  container: `scripts/check_fresh_install.sh` (apt, the astral uv installer, a git install —
  all network-bound) now runs in CI's own `fresh-install` job, with its own deadline and one
  retry. The script times each phase (apt, uv installer, git install, dashboard up) under its
  own deadline; a phase that hangs exits 75 and names itself, and two hangs in a row end the
  job red as "infra: did not complete", never green. The prereqs image is tagged by a hash
  of its Dockerfiles and cached between CI runs, so apt and the uv installer are not fetched
  on every PR. (CMX-444)
