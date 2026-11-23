### Fixed

- **A focused Wall pane keeps the keyboard.** Another pane loading or reconnecting took it: ttyd
  focuses its terminal whenever its socket opens, so the ring, the cursor and your keys went to
  that pane. A pane now takes focus by itself only when you are not typing in another pane or a
  dashboard field. And the 30 s refresh no longer reloads every pane when a window opened or
  closed just before it; nor, in single-pane view, the open pane. (CMX-493)
