### Changed

- **The device you're using sets a terminal's size.** A tmux window has one size, and with a
  desktop tab left open and visible the phone in your hand got the desktop's width and had to
  scroll sideways. Now each pane reports user input (a key, a tap or click, a scroll, a keybar
  key or Paste, focusing the pane from the page), and the client with the latest input is the
  only one `window-size largest` counts: every other client of that window gets `ignore-size`,
  visible or not. A resize alone is never input. With no input for
  `CHELA_TERM_ACTIVE_INPUT_MINUTES` (default 10) the largest visible client wins again, and
  typing at the desk takes the size straight back. A presented window keeps its fixed size.
  (CMX-476)
