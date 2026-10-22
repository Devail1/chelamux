### Changed

- **The pane footer shows in every pane mode.** A pane filling the phone's screen hid its
  footer; it now keeps the window id, model, branch and context % there too, as on a wall
  tile and a maximized pane. (CMX-242)
- **The focus ring no longer hides the context bar.** The thin context-% strip along a pane's
  bottom edge sat under the focused pane's highlight, so it vanished on exactly the pane you
  were looking at. It is drawn inside the ring now. (CMX-242)
- **The Minimized tray is one row.** It used to wrap to a second and third row as panes were
  minimized, taking height from every pane on the wall. Its chips now scroll sideways (a
  vertical mouse wheel scrolls them too), a fade marks the edge that hides more, and the label
  counts them: `Minimized (5)`. (CMX-242)
- **The rest of the Wall's chrome is React** (CMX-212 P27, P28, P30, P32): the pane footer
  (the window id that copies itself, model, PR, cost, branch, context % and the Files chip),
  the Answer / Approve / Review bar and the recap line; the Minimized tray and the lazy dock for
  dispatched workers; the layout toolbar (presets, lock, auto-arrange, Focus); and the wire
  that puts two agents in a room. The footer's branch and PR flag, the action bar's marks and
  the wire's toast are icons or plain text instead of emoji. (CMX-242)
