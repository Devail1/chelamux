### Added

- **The nightly mutation sweep groups its survivors (2 of 3).** Survivors are grouped by
  category — the mutation's shape plus the guard line's stripped text — so one weak guard
  copied to five files is one group, and one line that survives two shapes is two. Each group
  lists every same-shape site in the checkout, found with a fixed-string `git grep -F` (a
  line holding `.*[(` or `v[0]` keeps all its sites). A group whose sites cannot be listed, or
  whose listing misses one of its own survivors, is not filed, and the next night retries it.
  A night with no survivor that left mutations past the budget now reports `unrun`, and one
  with a merged file no mutator handles reports `unknown`. Neither reports `clean`, and
  `chela judge nightly-sweep` exits 2 for both. (CMX-304)
