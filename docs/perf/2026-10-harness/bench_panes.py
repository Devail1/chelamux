"""Current per-window probe (what /api/agents + /api/agents/context do) vs the existing
batched sessions.panes() — wall + CPU incl. children, per call. Read-only."""

import time
import json
import resource
from chela import discovery, agent_manager, sessions


def cpu():
    a = resource.getrusage(resource.RUSAGE_SELF)
    b = resource.getrusage(resource.RUSAGE_CHILDREN)
    return a.ru_utime + a.ru_stime + b.ru_utime + b.ru_stime


def per_window():
    w = discovery.get_all_windows()
    for name, wid in w.items():
        cp = agent_manager.claude_pid(wid)
        agent_manager.window_type(wid, cp is not None)
    return len(w)


def batched():
    return len(sessions.panes(force=True))


out = {}
for fn in (per_window, batched):
    fn()
    t = time.perf_counter()
    c = cpu()
    R = 15
    for _ in range(R):
        n = fn()
    out[fn.__name__] = dict(
        windows=n,
        wall_ms=round((time.perf_counter() - t) / R * 1e3, 1),
        cpu_ms=round((cpu() - c) / R * 1e3, 1),
    )
print(json.dumps(out))
