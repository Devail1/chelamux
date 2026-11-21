### Changed

- **Sidebar: a session dragged within its own group under a sort other than Manual says why
  nothing happens.** Reordering rows exists only under the Manual sort (CMX-381). Under Last
  activity, Name or Created, a row held over its own group draws no insertion line between
  rows, its drop writes nothing, and the row shows *Reorder needs Manual sort*. Dragging a
  session onto another custom group still moves it under every sort. (CMX-458)
