### Fixed

- **The Wall's focus-ring poll now stops when you leave the Wall.** Entering the Wall
  starts a 250 ms poll that marks the pane holding the keyboard. Leaving the Wall stopped
  its 4 s refresh tick but not this poll or its window `focus` listener, so both kept
  running on every other view for the life of the page. They now stop with the tick, and
  the App's root lets go of every view's timers when it unmounts. This also fixes an
  intermittent Vitest failure ("ReferenceError: document is not defined" in
  `paneframe/lib/focus.ts`), where the leaked poll ticked after the test environment had
  been torn down. (CMX-446)
