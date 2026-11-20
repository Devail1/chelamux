### Fixed

- **A reboot no longer loses window names, queued titles or a session's Telegram topic.**
  A name set through the dashboard (a rename, or Suggest names) and a title Suggest names
  queued for an idle moment lived only as tmux window options, so they died with the tmux
  server. Both are now also kept by session id (`session-names.json`), and the `chela run`
  daemon puts them back on whichever window that session is restored into: the name is
  re-applied (only if no other window holds it), and the title is queued again and typed
  once the session is idle. A title the transcript already shows as applied is dropped.
  Auto-topics now records the session each topic is bound for, and a window that needs a
  topic first takes back the topic its session had, before the folder match and before
  creating one. A tmux restart closes a dead binding's topic but never deletes it, even
  with "delete a topic when its window closes" on. New `chela telegram rebind @N THREAD`
  moves a window that already has a topic onto another one (for a session that came back
  in a new topic). (CMX-450)
