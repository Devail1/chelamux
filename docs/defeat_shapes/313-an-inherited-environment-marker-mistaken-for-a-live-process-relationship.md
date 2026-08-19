## 313. An inherited environment marker mistaken for a live process relationship

**Assertion form:** a discriminator wants to answer "is THIS process, right now, managed by
system X" (a pm2 service, a supervisor, a container runtime) and reaches for "does an env var
X sets appear in `os.environ`" (`PM2_HOME`, `pm_id`, a similar marker). It looks reliable
because X really does set that var on every process it directly spawns.

**Mutation that defeats it:** nothing needs to change in the code at all — the bug is already
live on any host where a *descendant* of an X-managed process later gets reparented away from
X (a supervised process starts a long-lived child, that child's own children survive after the
supervised process exits, a daemonized server, a `tmux` session that outlives whatever spawned
it). Environment variables are copied at fork/exec time and never cleared by reparenting, so
every descendant keeps answering "yes, X manages me" forever, long after the live relationship
ended. The check now can't tell "X manages me right now" from "X touched my lineage at some
point in the past" — the exact two states it exists to separate.

**Guard form that survives:** ask the CURRENT process tree, not an inherited variable. Read the
managing system's own live record of itself (its daemon's pid, from its own lock/pid file) and
walk `/proc`'s real parent-pid chain from `os.getpid()` up to that pid — an env var can lie
about the present; live ancestry cannot, because it is recomputed from kernel state on every
call. Also handle "the managing system was never installed at all" as *cannot confirm*, not
*confirmed absent* — the guard must never suppress on the many-orders-more-common case where the
whole discriminator legitimately doesn't apply.

**Found:** CMX-313 round 2 (2026-08-19), PR #390. `process.node_ipc_env`'s
`_process_node_ipc_env_applies` gated on `$TMUX_PANE` to tell an interactive agent pane apart
from a pm2-managed service, but `$TMUX_PANE` itself has exactly this defeat shape — the
reviewer measured `chela-dashboard` carrying a stale `TMUX_PANE` live on the fleet, which is
the documented reason every chela pm2 service must pin `CHELA_TMUX_SESSION` in the first place.
The obvious-looking round-2 fix ("also check for `PM2_HOME`/`pm_id` in the environment") would
have repeated the identical mistake one layer down: `chela-agent-terminals` (a pm2 service)
spawns the `chela` tmux SERVER every real agent pane lives in, so those pm2 env vars are
inherited into every agent pane too, at server-creation time — and, like `$TMUX_PANE`, never
get cleared by the tmux server's later reparenting to init. Closed by reading pm2's own
`$PM2_HOME/pm2.pid` lock file for its daemon's live pid and walking `/proc`'s real ancestry
from `os.getpid()` (`_pm2_manages_this_process`) instead of trusting anything in `os.environ`.
