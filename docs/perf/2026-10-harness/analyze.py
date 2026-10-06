"""analyze.py RUNDIR — per-endpoint table, spawn table, py-spy hot functions."""

import json
import sys
import collections
import statistics as st
import os

D = sys.argv[1]
skip_first_s = 60
rows = [json.loads(ln) for ln in open(f"{D}/dash.requests.jsonl") if ln.startswith('{"t"')]
t0 = rows[0]["t"] + skip_first_s
t1 = rows[-1]["t"]
rows = [r for r in rows if r["t"] >= t0]
mins = (t1 - t0) / 60


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


by = collections.defaultdict(list)
for r in rows:
    by[r["ep"]].append(r)
print(f"window {mins:.1f} min, {len(rows)} requests ({len(rows) / mins:.0f}/min)")
print(
    f"{'endpoint':38} {'/min':>6} {'p50ms':>7} {'p95ms':>7} {'cpu/req':>7} {'cpu s/min':>9} {'spawn/req':>9} {'spawnms':>7} {'KB/req':>7} {'same%':>6}"
)
tot_cpu = 0
out = []
for ep, rs in by.items():
    if rs[0]["stream"] or ep.startswith("/term/<wid>/ws") or ep == "/api/events":
        continue
    # unchanged-since-last-poll, per client+query
    last = {}
    same = n = 0
    for r in rs:
        k = (r["cl"], r["q"])
        if k in last:
            n += 1
            same += last[k] == r["h"]
        last[k] = r["h"]
    cpu = sum(r["cpu_ms"] for r in rs) / 1e3
    tot_cpu += cpu
    out.append(
        (
            cpu,
            ep,
            len(rs) / mins,
            pct([r["ms"] for r in rs], 50),
            pct([r["ms"] for r in rs], 95),
            st.mean(r["cpu_ms"] for r in rs),
            cpu / mins,
            st.mean(r["sp"] for r in rs),
            st.mean(r["sp_ms"] for r in rs),
            st.mean(r["b"] for r in rs) / 1024,
            100 * same / n if n else float("nan"),
        )
    )
for o in sorted(out, reverse=True)[:22]:
    print(
        f"{o[1][:38]:38} {o[2]:6.1f} {o[3]:7.1f} {o[4]:7.1f} {o[5]:7.1f} {o[6]:9.2f} {o[7]:9.1f} {o[8]:7.1f} {o[9]:7.1f} {o[10]:6.0f}"
    )
print(f"total handler thread-CPU (excl. SSE/ws/child procs): {tot_cpu / mins:.2f} s/min")
api = [r for r in rows if r["ep"].startswith("/api/") and not r["stream"]]
print(
    "api requests/min per client:",
    {
        c: round(sum(1 for r in api if r["cl"] == c) / mins, 1)
        for c in sorted({r["cl"] for r in api})
    },
)
print(
    "api bytes/min per client KB:",
    {
        c: round(sum(r["b"] for r in api if r["cl"] == c) / mins / 1024, 1)
        for c in sorted({r["cl"] for r in api})
    },
)
print("max threads seen:", max(r["thr"] for r in rows))
for tag in ("dash", "daemon", "tg"):
    p = f"{D}/{tag}.spawns.json"
    if not os.path.exists(p):
        continue
    sp = json.load(open(p))["spawns"]
    total = sum(s["n"] for s in sp)
    print(
        f"\n[{tag}] spawns total {total} (~{total / (mins + skip_first_s / 60):.0f}/min over whole run), wall {sum(s['wall_s'] for s in sp):.0f}s"
    )
    for s in sp[:12]:
        print(f"  {s['n']:6} {s['wall_s']:7.1f}s  {s['ctx'][:34]:34} {s['cmd']}")
