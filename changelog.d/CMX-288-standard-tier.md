### Changed

- **A `standard` risk tier: product and UI PRs get a judge round with no held-out experiments.**
  `chela/risktier.py` now sorts a judged PR into `core` (it touches the dispatcher, the judge,
  the workflow loader, the sources or `chela/context.py`; a workflow adds paths with
  `merge.core`) or `standard` (everything else). A standard run asks the judge for no held-out
  experiments and runs any it tags anyway as visible; a core run keeps today's quota. The tier
  is stamped on the run and shown by `chela judge show`. Prose and always-human are unchanged.
  (CMX-288)
