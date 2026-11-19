### Added

- **Sidebar: a session's child sessions nest under it, and you can pin a session.** A session
  another session launched (`chela spawn`, or its first cross-session message) is drawn only
  under its parent, indented with a guide line; the parent row gets a chevron and a count, and
  folding it is remembered on this device by session, so a restart that renumbers the windows
  keeps it. Children of children nest one level further, and the indent stops growing after
  three levels so a phone keeps every row's ⋯ on screen. Siblings follow the sort; under
  Manual they drag into their own order. Dragging the parent onto a custom group moves its
  children with it. **Pin** / **Unpin** in a row's ⋯ menu puts the session (and its children)
  in a **Pinned** section at the top. (CMX-390)

### Changed

- **Archiving a parent session archives its child sessions, and unarchiving it brings them
  back.** The dashboard does this on the server, so every device agrees, and the Archived
  section keeps the subtree nested. A pinned session is never archived, by hand or with its
  parent: its Archive reads "Pinned: unpin to archive", and a pinned child stays out of its
  parent's archive (with its own children) and shows under Pinned. Dispatched runs are
  unchanged. (CMX-390)
