### Changed

- **The sidebar layout now follows you across devices.** The group order, custom groups and
  their members, and the View settings (group by, sort, status) are stored by chela
  (`/api/sidebar/layout`) instead of each browser's localStorage, so the phone and the laptop
  show the same sidebar. Collapsed groups, the collapsed sidebar and the Archived section's
  open/closed state stay per device. The first browser to load the new version imports its
  old local layout once; later devices adopt that one. (CMX-82, #649)
