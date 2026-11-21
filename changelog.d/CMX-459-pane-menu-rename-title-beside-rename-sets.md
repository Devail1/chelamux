### Added

- **Set a session's Claude title by hand.** Double-click the title in a pane header to edit it
  in place (Enter saves, Esc cancels, at most 40 characters); the name beside it still renames
  the window. For touch, **Rename title…** sits after **Rename** in the pane ⋯ menu and in the
  sidebar row ⋯ menu. It uses the same title path as *Suggest names*: `/rename` is typed only
  into an idle session with no dialog and an empty prompt, otherwise the title is queued, shown
  as pending and applied once the session is idle. A pane with no Claude in it refuses, and the
  menu item is disabled there. (CMX-459)

### Changed

- **The pane header's order is fixed: the window name first (bold), Claude's title second
  (dim).** Before, the title led any window nobody had renamed, so the two swapped places from
  pane to pane. The sidebar row, the dock and the palette still show one label, as before.
  (CMX-459)
