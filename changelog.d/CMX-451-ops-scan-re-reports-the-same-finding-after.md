### Fixed

- **The ops scan no longer re-sends a judge-baseline flake finding it already reported.**
  "`<test>` failed judge baselines of N tasks in 24h" carried no evidence signature, so the
  scan re-sent it every 6 hours (and so after every daemon restart past that) for as long as
  the tasks stayed in the 24 h window, even after the cause was fixed. It is now signed by
  its task set, like the CI quarantine finding: the same tasks send nothing, a new task sends
  it once, and a task's later round only is not news. A finding already notified before this
  change adopts its signature on the first scan instead of being sent again. (CMX-451)
