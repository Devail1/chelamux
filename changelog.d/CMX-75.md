### Changed

- **Archiving a session in the sidebar now closes it, like the Claude desktop app, and
  Unarchive resumes it.** Before, Archive only hid the row in one browser: the window
  kept running, and the phone and the laptop disagreed about what was archived. The
  archive is now chela state (`~/.chela/sidebar-archive.json`), so every device shows the
  same set. Archiving a finished session records its session id, folder, window name and
  Remote Control name, then closes the window; its Telegram topic is closed. Unarchive
  opens a new window in the same folder under the same name, runs `claude --resume`, and
  reopens the same Telegram topic. Archive is refused when the session id cannot be
  determined, and for a busy or waiting session or the orchestrator. A settled dispatched
  run is only hidden, because its windows belong to the dispatcher. Archived rows now sit
  in one collapsed **Archived (N)** section at the bottom of the sidebar (View ▸ Status
  All or Archived), not inside their folders. A browser's old archive list is imported
  once, as hidden rows, without closing anything. (CMX-75)
