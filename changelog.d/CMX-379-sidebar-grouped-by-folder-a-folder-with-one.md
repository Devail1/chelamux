### Changed

- **Grouped by folder, a folder with one session is now a single sidebar row.** Most
  folders hold one session, and each one took a group header, a row and a gap, so eight
  sessions filled the screen. Now such a folder has no header. Its session is one row with
  the folder name on the dim second line (`~` for the home folder, `<project> · <task id>`
  for a worktree). Folders with two or more sessions keep their header. The bare row keeps
  its place in the group order (folder name, or your Move up / Move down order), and its
  `⋯` menu holds what the header had: New session in this folder, Move up / Move down and
  Collapse / Expand all. A folder that gains or loses a second session switches between the
  two forms on the next render. Custom groups and the other grouping modes are unchanged.
  (CMX-379)
