### Added

- **The nightly mutation sweep files its survivors and tells the inbox (3 of 3).** Each
  survivor group becomes ONE tracker issue, labelled `risk:normal` and `sweep`, listing every
  mutation that survived and every same-shape site. A group is skipped only when every
  surviving mutation in it was filed before; a new site of a filed category files the group
  again. The filed keys are written after each issue, so a filing that fails part-way keeps
  the ones before it. A tracker that cannot create issues records what it would have filed in
  `$CHELA_DIR/sweep-nightly/would-file.jsonl` instead. Each run queues ONE inbox notice: the
  issues filed (or "nothing filed"), already filed, not filed and why, clean, or skipped and
  why, with the run's UNRUN and UNKNOWN counts. (CMX-305)
- **Linear's `create_issue` takes label names.** They are added to a parent's inherited labels,
  never in place of them. A missing label is created on the team, and is used only when Linear
  answers `success: true`. A label that can be neither found nor created is a warning, and the
  issue is still created. (CMX-305)
