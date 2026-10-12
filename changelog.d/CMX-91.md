### Changed

- **Docs: README leads with the gate.** The README top now pitches the merge gate (tracker
  dispatch → mutation-testing judge bound to the PR head → `chela merge` refusing without a
  clean verdict, one-tap Telegram override), puts the demo right under the banner, has one
  install block with a one-line requirements list, and states plainly that agents run
  unsandboxed by default (the sandbox is opt-in per workflow). The landing hero carries the
  same pitch. (CMX-91)
