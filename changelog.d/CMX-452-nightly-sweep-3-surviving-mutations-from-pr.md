### Fixed

- **Tests now pin three sidebar drag guards the nightly mutation sweep found unguarded.**
  Moving a session between custom groups keeps every other session's last-seen stamp and
  drops or re-stamps only its own; Move to group with an id that names no group unassigns
  the session instead of writing the unknown id; and outside Custom groups, a row dragged
  below every folder (which reads as Ungrouped) assigns nothing. (CMX-452)
