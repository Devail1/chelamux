### Fixed

- **Archiving a session records the folder its transcript names, not a live pane read.** The
  sidebar archive took a session's cwd from the pane (`#{pane_current_path}`) when it could not
  read a claude process's own cwd. tmux can report a just-opened pane's path before the pane
  has moved into the project (the tmux server's own cwd), and a pane path moves whenever its
  shell `cd`s, so the record could name the wrong folder and Unarchive would resume the session
  where `claude --resume` cannot find it. The archive now takes the cwd from the session's own
  transcript first, and falls back to the pane only when the transcript names none. The flaky
  `test_archive_closes_an_idle_session_and_unarchive_resumes_it_in_a_new_window` now waits for
  the resumed claude to launch before it reads the new pane's path. (CMX-508)
