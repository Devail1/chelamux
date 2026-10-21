### Changed

- **The Decisions inbox and the task-detail modal are React (CMX-212 P07-P09).** The inbox
  button, its owner dot and unread badge, the log dialog (owner chip, re-register, search,
  click-through) and the task modal (Brief and Review tabs, rounds, details) run on the
  dashboard's UI kit: real dialogs with focus trapping, and keyboard tabs. The re-register
  picker and button are the kit's Select and Button. (CMX-228)
- **The owner chip in the Decisions inbox has its border and padding back.** A comment in
  the stylesheet closed early and swallowed the chip's own rule. (CMX-228)

### Security

- **A brief's markdown links only to http(s), mailto or relative URLs.** A task brief or a
  judge report holding `[x](javascript:...)` (or `data:`, `vbscript:`, `file:`) rendered a
  live link in the task modal; it now renders as plain text. (CMX-228)
