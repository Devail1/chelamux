### Changed

- **Grouped by Custom groups, dispatched runs have their own Runs group.** Every run now
  lands in one automatic **Runs** group pinned at the top of the sidebar, whether or not
  you have made any custom groups, instead of mixing with your sessions in Ungrouped. A
  run's judge stays inside the run's row. A run can't be dragged or moved into a custom
  group (it goes away when the run ends), and the orchestrator stays wherever you put it.
  Runs is hidden when there are none, and always with `CHELA_DISPATCH=off`. Grouped by
  folder, the Dispatched section is unchanged. (CMX-438)
