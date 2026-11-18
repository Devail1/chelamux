### Fixed

- **A long press on a sidebar list that is still gliding from a flick no longer starts a drag
  that vanishes under the finger.** The browser sends a touch that lands mid-glide as an
  uncancelable sequence, so the drag could not hold the list: the first move panned it and
  cancelled the drag. Such a touch now only stops the glide; a long press on a list at rest
  drags as before. This was also the cause of the intermittent CI failure in the sidebar drag
  browser test, which now waits for the glide to end instead of sleeping. (CMX-435)
