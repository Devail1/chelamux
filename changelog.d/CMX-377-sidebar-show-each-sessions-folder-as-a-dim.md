### Changed

- **Each sidebar session row now names its folder on a dim second line.** Session titles
  do not say which project a session belongs to, so most rows could not be placed at a
  glance. The second line shows the session's folder name (`~` for the home folder). When
  two folders share a name, it shows enough of the path to tell them apart
  (`projects/foo` and `work/foo`). A dispatched worktree reads `<project> · <task id>`
  (`chelamux · CMX-374`). The line is hidden while the sidebar is grouped by folder,
  because the group header already names the folder. The title, badges, crown and `⋯`
  menu stay on the first line, and each line truncates on its own. (CMX-377)
