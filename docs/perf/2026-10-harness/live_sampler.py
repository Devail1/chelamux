"""Read-only /proc sampler: CPU (self+reaped children), RSS, threads, fds, child-spawn
census (unique child pids seen at 20Hz). Never signals or attaches."""

import os
import sys
import time
import json

pids = {k: int(v) for k, v in (a.split("=") for a in sys.argv[2:])}
dur = float(sys.argv[1])
TCK = os.sysconf("SC_CLK_TCK")


def stat(p):
    s = open(f"/proc/{p}/stat").read()
    f = s[s.rfind(")") + 2 :].split()
    return dict(ut=int(f[11]), st=int(f[12]), cut=int(f[13]), cst=int(f[14]), thr=int(f[17]))


def rss(p):
    for ln in open(f"/proc/{p}/status"):
        if ln.startswith("VmRSS"):
            return int(ln.split()[1])


def kids(p):
    out = set()
    for t in os.listdir(f"/proc/{p}/task"):
        try:
            out |= set(open(f"/proc/{p}/task/{t}/children").read().split())
        except OSError:
            pass
    return out


def cmd(c):
    try:
        return open(f"/proc/{c}/cmdline", "rb").read().split(b"\0")[:3]
    except OSError:
        return None


t0 = time.time()
first = {k: stat(p) for k, p in pids.items()}
seen = {k: {} for k in pids}
samples = {k: [] for k in pids}
last = 0
while time.time() - t0 < dur:
    for k, p in pids.items():
        for c in kids(p):
            if c not in seen[k]:
                cm = cmd(c)
                seen[k][c] = b" ".join(cm).decode(errors="replace")[:80] if cm else "?"
    if time.time() - last > 10:
        last = time.time()
        for k, p in pids.items():
            st = stat(p)
            samples[k].append(
                dict(
                    t=round(time.time() - t0),
                    rss_kb=rss(p),
                    thr=st["thr"],
                    fds=len(os.listdir(f"/proc/{p}/fd")),
                )
            )
    time.sleep(0.05)
el = time.time() - t0
res = {"elapsed_s": el}
for k, p in pids.items():
    a, b = first[k], stat(p)
    from collections import Counter

    res[k] = dict(
        cpu_self_s=(b["ut"] + b["st"] - a["ut"] - a["st"]) / TCK,
        cpu_children_s=(b["cut"] + b["cst"] - a["cut"] - a["cst"]) / TCK,
        spawns_seen=len(seen[k]),
        spawn_kinds=Counter(
            v.split(" ")[0].rsplit("/", 1)[-1] + " " + (v.split(" ")[1] if " " in v else "")
            for v in seen[k].values()
        ).most_common(8),
        rss_kb=[s["rss_kb"] for s in samples[k]][::6],
        thr=sorted({s["thr"] for s in samples[k]}),
        fds=sorted({s["fds"] for s in samples[k]}),
    )
print(json.dumps(res, indent=1))
