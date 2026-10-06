### Changed

- **The Telegram pane watch re-captures only panes that changed.** Each tick it reads every
  window's `#{window_activity}` in ONE `tmux list-windows` call and runs `capture-pane`
  only for windows whose stamp moved since their last capture (or whose stamp is in the
  same second that capture began, since the stamp is whole seconds). Unchanged windows are
  handed their last captured text, so every detector, hook-log lookup and re-post backoff
  still runs every tick. A full sweep re-captures every window at least every 30 s as a
  backstop for changes that write no output. The transcript relay and the pane loop now
  ask tmux for the server epoch once per tick (`epoch.per_tick()`), not once per window.
  Measured at 40 idle windows: `capture-pane` spawns 1,160 → 92 per run and the
  service's CPU 10.1–10.3% → 5.9–6.6% of a core. (CMX-18)
