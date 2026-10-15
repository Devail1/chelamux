### Fixed

- **Unarchive opens one window, even when two taps race.** A second Unarchive of the same
  session while the first is still spawning is refused (409) instead of opening a second
  window; a failed spawn releases the claim. Archive keys imported from a browser's old local
  state are stamped with the tmux server's epoch, so a hidden `@N` from a dead tmux server no
  longer hides an unrelated new window. (CMX-78, #654)
- **The Usage view's 30-minute window no longer empties after UTC midnight.** It scanned
  transcripts only from the start of the UTC day, so for the first half hour of each day the
  30-minute consumers lost everything from before midnight. (CMX-78, #654)
