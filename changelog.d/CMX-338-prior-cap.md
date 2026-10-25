### Fixed

- **A re-judge re-runs at most a cap's worth of the previous round's experiments.** A round
  now runs at most twice the risk-level cap: up to the cap of new experiments, plus up to the
  cap re-run from the previous round, its survivors first. Before this, from the third round
  the previous round's re-runs could fill the whole grown cap and leave the judge's new
  experiments unrun. (CMX-338)
