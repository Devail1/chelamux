### Fixed

- **The live archive negative control no longer races the pane it checks.**
  `test_archive_records_the_transcripts_cwd_while_the_pane_has_not_reached_the_project` read
  the new pane's path straight after `new-window`, so on a slow runner tmux could report it
  before the pane's process had moved into its start folder, and the test failed its own
  precondition. The pane now writes a ready marker from the start folder and blocks on a FIFO
  until the test has archived it, so the precondition holds by construction. (CMX-512)
