### Added

- **`CHELA_DISPATCH=off`: dispatch off by choice.** An explicit switch for an install that
  runs no dispatcher. The daemon announces `dispatch: OFF (by choice, CHELA_DISPATCH=off)` at
  INFO and `chela doctor` reports it as information instead of a warning; the dashboard hides
  the Work board and nav row, **New task** and the sidebar's **Dispatched** section, and
  Settings shows one line in place of the *Work dispatcher* row. An empty
  `CHELA_DISPATCH_WORKFLOWS` without the switch still warns, exactly as before. The switch
  with workflows configured is a contradiction: doctor reports it as an ERROR and dispatch
  stays off. (CMX-388)
