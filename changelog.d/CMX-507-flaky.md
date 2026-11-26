### Fixed

- **Flaky `test_a_yielded_holder_re_queues_at_its_runs_rank_and_claim`.** The test read the slot
  queue right after the yielded holder's re-rank, assuming the judge it yielded to had already
  claimed the slot. On a loaded runner the judge was still an open wait, so the queue listed two
  waiters. It now waits, with a bound, until the judge holds the slot before reading the queue.
  (CMX-507)
