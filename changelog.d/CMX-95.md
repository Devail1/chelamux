### Changed

- **Restarting the daemon waits only for its current tick.** The daemon holds a lock
  (`$CHELA_DIR/daemon-tick.lock`) for each tick, and `chela update` / the dashboard's Update
  takes it before restarting `chela-daemon`, so a restart never lands mid-claim, mid-spawn or
  mid-DB-write. The wait is bounded at 60 s; past that, or when the lock cannot be read, the
  restart refuses with the reason. Other services never wait. (CMX-95, #679)
