### Changed

- **The Work view on desktop has one header row.** The two rows above the board (the tabs
  with four coloured buttons, then the project pills) are one: the current project as a
  dropdown (search, All projects, one row per project with its running and queued counts, and
  "dispatch on" or "paused" under the title), Board | Runs | Schedules, Pause dispatch as a quiet
  outline that reads Resume while held, New task as the one primary button, and a ⋯ menu with
  Init a repo, Pause all agents (which now asks to confirm), the Board | Rows layout and the
  board's column options. On the board, Todo, In Progress and In Review are fixed 320px columns;
  Backlog and Done (and Archived) collapse to narrow rails at the edges and open on a click,
  and the board remembers which ones you left open for each project. The "Hidden columns"
  panel is gone: hide a column from ⋯ › Column options. Cards are the phone's compact card:
  the branch, merge, delete and Close & requeue live in the card's ⋮. Rows on desktop is a
  table (ID, title, state, PR, CI, risk, age) grouped by status, sortable by any column head and
  walkable with the arrow keys. (CMX-316)
