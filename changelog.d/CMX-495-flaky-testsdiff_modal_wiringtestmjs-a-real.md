### Fixed

- **Flaky `tests/diff_modal_wiring.test.mjs`.** The suite read the diff modal's file rows after
  one event-loop turn, which React's scheduler can miss on a loaded runner. It now waits, with
  a bound, for the rows, and checks that they are the clicked session's. (CMX-495)
