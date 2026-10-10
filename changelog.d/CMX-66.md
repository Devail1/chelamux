### Added

- **A View menu for the dashboard sidebar, like the Claude desktop app's.** The sliders
  button on the Sessions header picks what shows and how it is laid out:
  **Status** (Active / All / Archived), **Environment** (Interactive, Dispatched agents,
  Judges, Background sessions; all but Judges by default), **Last activity** (1d / 7d /
  30d / All; default 7d), **Group by** (Date, Folder, State, Custom groups, None),
  **Sort by** (Last activity, Name, Created), plus **Show empty groups** and **Show PR
  status** (a `#N` PR badge on dispatched-run rows). Custom groups are created, renamed
  and deleted in the menu and assigned from any row's menu ("Move to group…"); they
  follow the window, not its name. Filters never hide a session that is working or
  waiting on you, or the orchestrator. Choices are kept per browser. Submenus open by
  tap, so the menu works on a phone. `/api/agents` now carries each window's
  `last_activity` and `created`. (CMX-66)
