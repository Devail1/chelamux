### Fixed

- **A pane's terminal no longer jumps in font and size while it opens.** ttyd painted at its
  own default font, then at the server's, then at your Settings > Terminal size (after a
  one-step size flicker). The terminal now stays hidden until your font and size are set and
  rendered, and shows within 1.5 s even if a font never loads. Your size and face are also
  handed to ttyd's own preferences, so a reconnect no longer drops back to the server
  default. (CMX-321)
