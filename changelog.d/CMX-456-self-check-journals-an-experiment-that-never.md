### Fixed

- **A self-check experiment that never applied no longer blocks the push of a clean file.**
  An experiment whose anchor did not match (or matched more than once) used to be written to
  the mutation journal anyway, with the clean file's own bytes as the "mutation", so
  `request-push` refused a committed file whose `git diff HEAD` was empty. Such an experiment
  is now INVALID and leaves no marker and no journal entry. Journal entries now record the
  pre-mutation bytes too, and an entry that never changed its file is dropped (and logged) at
  hand-in instead of refusing it; a real leftover mutation still refuses. The refusal no
  longer tells an agent to edit the journal by hand: it points at the new
  `chela judge journal --prune-stale <run>`, which re-checks each entry against the file
  before dropping it and never drops a real mutation. (CMX-456)
