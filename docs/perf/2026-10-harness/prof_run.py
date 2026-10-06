"""Instrumented launcher: `python prof_run.py <tag> <chela args...>`.
Counts every subprocess spawn (argv head, thread, flask endpoint) and, for the
dashboard, logs per-request wall/CPU/bytes/body-hash. Production code untouched."""

import sys
import os
import time
import json
import threading
import hashlib
import subprocess
import atexit
import collections

tag = sys.argv[1]
OUT = os.environ["PERF_OUT"]
os.makedirs(OUT, exist_ok=True)
_tl = threading.local()
spawns = collections.Counter()
spawn_wall = collections.Counter()
lock = threading.Lock()
_orig_init = subprocess.Popen.__init__


def _ctx():
    try:
        from flask import has_request_context, request

        if has_request_context():
            return request.url_rule.rule if request.url_rule else request.path
    except Exception:
        pass
    return "thread:" + threading.current_thread().name.split("-")[0].split(" ")[0]


def _popen_init(self, args, *a, **k):
    head = args if isinstance(args, str) else " ".join(str(x) for x in list(args)[:2])
    key = (_ctx(), head.rsplit("/", 1)[-1][:60])
    with lock:
        spawns[key] += 1
    _tl.n = getattr(_tl, "n", 0) + 1
    self._perf_key = key
    self._perf_t0 = time.perf_counter()
    return _orig_init(self, args, *a, **k)


subprocess.Popen.__init__ = _popen_init
_orig_comm = subprocess.Popen.communicate


def _comm(self, *a, **k):
    try:
        return _orig_comm(self, *a, **k)
    finally:
        if hasattr(self, "_perf_t0"):
            dt = time.perf_counter() - self._perf_t0
            with lock:
                spawn_wall[self._perf_key] += dt
            _tl.sw = getattr(_tl, "sw", 0.0) + dt


subprocess.Popen.communicate = _comm


def dump():
    with lock:
        rows = [
            {"ctx": c, "cmd": h, "n": n, "wall_s": round(spawn_wall[(c, h)], 3)}
            for (c, h), n in spawns.most_common()
        ]
    json.dump({"t": time.time(), "spawns": rows}, open(f"{OUT}/{tag}.spawns.json", "w"), indent=0)


def _dumper():
    while True:
        time.sleep(20)
        dump()


threading.Thread(target=_dumper, daemon=True, name="perfdump").start()
atexit.register(dump)

if sys.argv[2] == "dashboard":
    from flask import request, g
    from chela.dashboard import app as A

    reqlog = open(f"{OUT}/{tag}.requests.jsonl", "a", buffering=1)

    @A.app.before_request
    def _b():
        g._t0 = time.perf_counter()
        g._c0 = time.thread_time()
        _tl.n = 0
        _tl.sw = 0.0

    @A.app.after_request
    def _a(resp):
        try:
            streamed = resp.is_streamed
            body = b"" if streamed or resp.direct_passthrough else resp.get_data()
            rec = dict(
                t=round(time.time(), 3),
                ep=request.url_rule.rule if request.url_rule else request.path,
                q=request.query_string.decode()[:80],
                m=request.method,
                st=resp.status_code,
                ms=round((time.perf_counter() - g._t0) * 1e3, 2),
                cpu_ms=round((time.thread_time() - g._c0) * 1e3, 2),
                b=len(body),
                h=hashlib.sha1(body).hexdigest()[:12] if body else None,
                stream=streamed,
                cl=request.headers.get("X-Perf-Client", "?"),
                sp=getattr(_tl, "n", 0),
                sp_ms=round(getattr(_tl, "sw", 0.0) * 1e3, 1),
                thr=threading.active_count(),
            )
            reqlog.write(json.dumps(rec) + "\n")
        except Exception as e:
            reqlog.write(json.dumps({"err": repr(e)}) + "\n")
        return resp

    if os.environ.get("PERF_SERVER") == "waitress":
        import waitress

        A.app.run = lambda host, port, **k: waitress.serve(
            A.app, host=host, port=port, threads=int(os.environ.get("PERF_WAITRESS_THREADS", "16"))
        )
from chela import main as M  # noqa: E402 — after the hooks above are installed

sys.argv = ["chela"] + sys.argv[2:]
M.main()
