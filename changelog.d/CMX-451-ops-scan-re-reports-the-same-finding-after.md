### Fixed

- **The ops scan no longer re-sends a judge-baseline flake finding it already reported.**
  "`<test>` failed judge baselines of N tasks in 24h" carried no evidence signature, so the
  scan re-sent it every 6 hours (and so after every daemon restart past that) while its tasks
  stayed in the 24 h window, even after the cause was fixed. It is now signed by its task
  set, like the CI quarantine finding, and a signed finding is re-sent only when it names a
  task no earlier notice did: the same tasks, a task's later round, or a task aging out of
  the window send nothing; a new task sends it once. A finding already notified before this
  change adopts its signature on the first scan instead of being sent again. (CMX-451)
