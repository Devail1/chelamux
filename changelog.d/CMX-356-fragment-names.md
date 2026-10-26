### Fixed

- **A changelog fragment named after a reused issue number no longer turns the suite red
  late.** Issue numbering restarted, so a new task often shares its number with an old one
  that already shipped, and its bare `changelog.d/CMX-<id>.md` read as an already-released
  fragment. Fragments are now named `CMX-<id>-<slug>.md` (slug from the branch), and
  `chela task-finished` refuses a bare name that collides before any suite runs, with a
  rename hint. A fragment whose text already shipped (bare or slugged) is told to go
  instead: renaming it would only hide it until it republishes. (CMX-356)
