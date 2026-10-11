### Changed

- **Sidebar: an idle session can be archived, and every row has its own Archive.** A live
  Claude session sitting idle (waiting for your next message) now counts in a group's
  "Archive all (N)", and each row's menu (right-click, or its ⋯) has an **Archive** item,
  enabled by the same rule and sent the same way. A busy session, one waiting on you, the
  orchestrator, a judge mid-battery, an unsettled run, and a session whose status cannot be
  read are still never archived. Dispatched runs are still only hidden, never closed. (CMX-81)
