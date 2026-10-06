"""spy.py RAWFILE [top] — py-spy raw (collapsed) -> self & inclusive time by function."""

import sys
import collections

self_c = collections.Counter()
incl = collections.Counter()
thr = collections.Counter()
total = 0
for line in open(sys.argv[1]):
    stack, _, n = line.rstrip().rpartition(" ")
    n = int(n)
    total += n
    frames = stack.split(";")
    thr[frames[0][:40]] += n
    fn = [
        f.split(" (")[0] + " (" + f.split(" (")[1].split(":")[0].rsplit("/", 1)[-1] + ")"
        if " (" in f
        else f
        for f in frames[1:]
    ]
    if fn:
        self_c[fn[-1]] += n
    for f in set(fn):
        incl[f] += n
top = int(sys.argv[2]) if len(sys.argv) > 2 else 25
print(f"total samples {total}")
print("-- by thread")
[print(f"{v / total * 100:5.1f}% {k}") for k, v in thr.most_common(8)]
print("-- self")
[print(f"{v / total * 100:5.1f}% {k}") for k, v in self_c.most_common(top)]
print("-- inclusive (chela/ only)")
[
    print(f"{v / total * 100:5.1f}% {k}")
    for k, v in incl.most_common(400)
    if any(
        m in k
        for m in (
            "app.py",
            "agent_manager",
            "discovery",
            "dispatcher",
            "sessionids",
            "transcripts",
            "sessions.py",
            "inbox",
            "event_log",
            "context",
            "monitor",
            "gatewatch",
            "panescan",
            "messenger",
            "main.py",
            "scheduler",
            "doctor",
            "runtime_truth",
            "reconcile",
            "resources",
            "epoch",
            "restore",
        )
    )
][:top]
