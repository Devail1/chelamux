### Fixed

- **The sidebar's context % badge and orchestrator crown show beside a long title.** A row's
  metadata strip shrank first and could shrink to nothing, so a long-titled session (the
  orchestrator at 64%, a 63% game prototype) drew no badge while a short-titled one at 51% did.
  The strip now gives way only down to its chips; a plain shell's long cwd chip still truncates.
- **A session whose folder is not known says "folder unknown"** on its folder line instead of
  drawing none (a live Claude with no session-feed entry yet has no cwd). (CMX-399)
