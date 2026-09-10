### Fixed

- **`chela wait`'s cadence guard blamed `_poll` for sleeps it never made, and went red on
  an unrelated PR.** `wait.time` *is* the stdlib `time` module, so the guard's stub —
  `monkeypatch.setattr(wait.time, "sleep", ...)` — replaced `time.sleep` **process-wide**
  and recorded every sleep any thread in that process happened to make. One leaked daemon
  thread calling `time.sleep(0.001)` was enough to fail the floor assertion with
  `[0.5, 0.5, 0.5, 0.5, 0.5, 0.001, 0.5, 0.5]` — a guard reporting a defect in code it was
  not watching. `_poll` now sleeps through a `wait._sleep` seam that only this module
  calls, and the stubs observe that instead, so an unrelated sleeper cannot enter the
  recording at all. Pinned by a counterweight that runs a noisy thread alongside the wait
  and asserts it stays out of the reading.
