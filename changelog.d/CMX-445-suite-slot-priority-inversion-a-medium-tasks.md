### Changed

- **The suite slot queue is ranked, and a self-check yields to a better-ranked waiter.** A
  free slot now goes to a judge round first, then to hand-ins by their issue's priority
  (Urgent, High, Medium, Low, then no priority), then to the nightly sweep. Equal ranks are
  still served first come, first served. Between two experiments, a self-check or a
  hand-in's sweep frees the slot when a judge or higher-priority hand-in is waiting, queues
  again, and resumes once the slot is back. A waiting judge gets the slot within one
  experiment instead of after the whole battery. The run row now records the issue's
  priority at claim time.

### Fixed

- **`chela doctor` no longer reports a judge that is waiting for a suite slot as past its
  wall.** As the watchdog already did, it leaves queued time out of the wall and reports it
  separately ("waited 73min for the suite slot").
