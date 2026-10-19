### Fixed

- **A daemon restart during a judge launch no longer costs a judge round.** On SIGINT/SIGTERM
  the daemon finishes the launch already in progress and launches nothing new: no judge, no
  rework, no claim. A launch interrupted anyway (a SIGKILL after PM2's `kill_timeout`, or a
  launch whose hook the restart's signal killed) no longer reads as "the judge's window
  disappeared" CANNOT VERIFY. The next daemon relaunches it as the same round, and no retry
  is spent. The run row records who is launching its judge (`judge_launch_owner`) until the
  launch is through. `examples/ecosystem.config.js` now gives `chela-daemon`
  `kill_timeout: 90000` and `treekill: false`. Copy both into your own ecosystem file, then
  `pm2 delete chela-daemon && pm2 start <ecosystem> --only chela-daemon` (a plain restart
  keeps the old kill settings). (CMX-263)
