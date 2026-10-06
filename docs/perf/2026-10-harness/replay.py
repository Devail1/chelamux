"""replay.py REQLOG PORT MINUTES OUT [sse_clients] — replay a recorded run's /api GET mix
(same per-client cadence) from threads; optional SSE holders. Client-side latency out."""

import json
import sys
import time
import threading
import urllib.request
import collections

log, port, mins, out = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
nsse = int(sys.argv[5]) if len(sys.argv) > 5 else 0
rows = [json.loads(ln) for ln in open(log) if ln.startswith('{"t"')]
t0 = rows[0]["t"] + 60
per = collections.defaultdict(list)
for r in rows:
    if (
        r["t"] < t0
        or r["m"] != "GET"
        or r["stream"]
        or not r["ep"].startswith("/api/")
        or "<" in r["ep"]
    ):
        continue
    per[r["cl"]].append((r["t"] - t0, r["ep"] + ("?" + r["q"] if r["q"] else "")))
res = []
lock = threading.Lock()
end = time.time() + mins * 60


def client(name, seq):
    start = time.time()
    span = seq[-1][0] + 1
    i = 0
    loop = 0
    while time.time() < end:
        off, path = seq[i]
        due = start + loop * span + off
        d = due - time.time()
        if d > 0:
            time.sleep(d)
        if time.time() >= end:
            break

        def go(path=path):
            t = time.perf_counter()
            ok = True
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=60).read()
            except Exception:
                ok = False
            with lock:
                res.append((path.split("?")[0], (time.perf_counter() - t) * 1e3, ok))

        threading.Thread(target=go, daemon=True).start()  # browsers issue concurrently
        i += 1
        if i == len(seq):
            i = 0
            loop += 1


def sse():
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/events", timeout=mins * 60 + 30)
        while time.time() < end and r.read(1):
            pass
    except Exception:
        pass


for _ in range(nsse):
    threading.Thread(target=sse, daemon=True).start()
ths = [threading.Thread(target=client, args=(c, s)) for c, s in per.items()]
[t.start() for t in ths]
[t.join() for t in ths]
time.sleep(3)
by = collections.defaultdict(list)
fails = 0
for ep, ms, ok in res:
    by[ep].append(ms)
    fails += not ok


def pct(x, p):
    x = sorted(x)
    return x[min(len(x) - 1, int(p / 100 * len(x)))]


allms = [m for _, m, _ in res]
summ = {
    "n": len(res),
    "fails": fails,
    "p50": pct(allms, 50),
    "p95": pct(allms, 95),
    "p99": pct(allms, 99),
    "by_ep": {
        e: {"n": len(v), "p50": round(pct(v, 50), 1), "p95": round(pct(v, 95), 1)}
        for e, v in by.items()
    },
}
json.dump(summ, open(out, "w"), indent=1)
print(json.dumps({k: v for k, v in summ.items() if k != "by_ep"}))
