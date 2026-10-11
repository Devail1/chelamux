### Changed

- **A window's name now follows its session's folder while chela still owns the name.** A
  window opened in `~` was named after your login and kept that name after its Claude
  moved to `~/projects/cute-animals`, even though the sidebar grouped it under that folder.
  The daemon now renames such a window to the new folder, adding `-2` and so on if the
  name is taken. It waits until the new folder has held for a full tick, and it reads the
  folder from the same place the sidebar does. The Telegram topic and the Remote Control
  name follow the new name. A name you chose, the orchestrator's window, and dispatched
  agent and judge windows are never renamed. (CMX-83)
