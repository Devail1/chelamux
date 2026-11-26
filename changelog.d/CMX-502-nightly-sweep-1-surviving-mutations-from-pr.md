### Fixed

- **Two sidebar-tree guards no test could tell from their corruption.** The archive cascade's
  "nothing below a refused parent" check was redundant with the parent table (which never keys a
  missing session), so disabling it changed nothing. The cascade now stops on the refusal itself
  and reads the archived session from the record, so the refused-parent test fails if the check
  goes. The chevron on an archived parent in the Archived section had no test; one now pins it
  and its fold. (CMX-502)
