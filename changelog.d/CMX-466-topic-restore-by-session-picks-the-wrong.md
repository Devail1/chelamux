### Fixed

- **Telegram: a relaunched session gets back the topic it was using, not an old duplicate.**
  When a session had two topics, the restore picked by the time a session id was first
  stamped on a topic, so it could pick a topic unused for weeks. It now picks the topic the
  session used last. And when a window dies because its session is relaunching in a new
  window, its topic moves to the new window directly, with its name and pinned title, instead
  of closing and reopening. A dead window's topic waits up to 90 seconds for its session to
  come back before it is closed. (CMX-466)
