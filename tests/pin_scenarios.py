"""CMX-394 scenario driver — the orchestrator pin vs two sessions sharing one cwd.

Run ONLY as a subprocess by ``tests/test_orchestrator_pin.py``, with an explicit env whose
``CHELA_DIR`` / ``CHELA_INBOX_FILE`` / ``CHELA_EVENTS_FILE`` / ``CLAUDE_CONFIG_DIR`` all point
into a temp dir. Nothing here reaches tmux, ``/proc`` or ``claude agents --json``: the pane
map, the window table, the tmux epoch, the status map and both send paths are stubbed, and
``/proc`` is a fixture tree. The session resolver itself (``chela.sessions.wid_for_session``
→ ``wid_claiming_session`` → Claude Code's session registry) is the REAL one — that is the
code the heal trusts, so it is the code under test.

Usage: ``python tests/pin_scenarios.py <scenario> <root>`` → one JSON object on stdout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HOME = "/home/someone"                       # the one cwd both sessions share
ORCH_SID = "ce2140f3-0000-4000-8000-000000000008"   # the orchestrator's own session
SIBLING_SID = "8b394f16-0000-4000-8000-000000000045"  # the other session in that cwd
OLD = "100-1000"                             # the tmux server that registered the pin
NEW = "200-2000"                             # the one running now


def _proc(root: Path, pid: int, ticks: str) -> None:
    d = root / "proc" / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    # A REALISTIC 52-field stat line (proc(5)): starttime is field 22, and fields 23-52
    # follow it (vsize, rss, … exit_code). A fixture with the ticks LAST let a reader taking
    # the last field pass every test and refuse every real registry entry (judge, d500fb1).
    (d / "stat").write_text(f"{pid} (claude) S " + " ".join(
        [str(4000 + i) for i in range(18)] + [ticks] + [str(9000 + i) for i in range(30)]) + "\n")


def _registry(pid: int, sid: str, wid: str, ticks: str) -> None:
    import os
    # Claude Code's real layout, spelled out — never via the helper under test (CMX-394 r5).
    reg = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "sessions"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": sid, "cwd": HOME, "procStart": ticks,
        "tmux": f"chela:{wid}.%{wid[1:]}", "kind": "interactive"}))


def main(scenario: str, root: Path) -> dict:
    from chela import epoch, event_log, inbox, messenger, sessions

    sessions.PROC = root / "proc"
    panes: dict[str, sessions.Pane] = {}

    def pane(wid: str, pid: int, sid: str, *, resumed: bool) -> None:
        ticks = str(pid * 7)
        _proc(root, pid, ticks)
        _registry(pid, sid, wid, ticks)
        panes[wid] = sessions.Pane(wid=wid, path=HOME, command="claude", claude_pid=pid,
                                   launched_in=HOME, resumed=sid if resumed else None,
                                   started=1.0)

    # The sibling: resumed with `claude --resume <sid>` in the SAME cwd, live and idle.
    pane("@45", 4545, SIBLING_SID, resumed=True)
    recorded = ORCH_SID
    if scenario == "own_session_reappears":
        # The orchestrator's own session came back in a NEW window after a tmux restart —
        # a plain `claude` (no --resume), so only the registry can say which session it runs.
        pane("@60", 6060, ORCH_SID, resumed=False)
    elif scenario == "no_identity":
        recorded = None
    elif scenario != "sibling_only":
        raise SystemExit(f"unknown scenario {scenario!r}")

    names = {wid: "liav" for wid in panes}
    statuses = {wid: inbox.IDLE for wid in panes}
    sessions.panes = lambda force=False: dict(panes)
    inbox.discovery.get_windows_by_id = lambda: dict(names)
    epoch.current = lambda: NEW
    inbox.status_snapshot = lambda: dict(statuses)
    sent: list[list[str]] = []
    messenger.send_peer = lambda wid, frm, text: messenger.PeerSendResult(False, None)
    messenger.send_tmux = lambda wid, text: (sent.append([wid, text]), True)[1]

    # The pin as the orchestrator left it: `@8`, issued by a server that has since died,
    # with one verdict queued for it.
    inbox.save({**inbox._empty(), "orchestrator": "@8", "orchestrator_epoch": OLD,
                "orchestrator_session": recorded, "orchestrator_name": "liav",
                "queue": [inbox._event("run_review", "📥 cmx-1 awaiting review — PR #1",
                                       {"task_id": "T1"})],
                "runs_seen": {"T1": "awaiting_review"}})
    runs = [{"task_id": "T1", "title": "a task", "status": "awaiting_review",
             "branch_name": "cmx-1", "window_name": "cmx-1", "pr_state": "open",
             "pr_url": "https://github.com/x/y/pull/1"}]
    inbox.tick({}, runs=runs)
    inbox.tick({}, runs=runs)
    store = inbox.load()
    return {"orchestrator": store["orchestrator"],
            "session": store["orchestrator_session"],
            "sent": sent,
            "queued": [e["kind"] for e in store["queue"]],
            "kinds": [e["type"] for e in event_log.read()["events"]]}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], Path(sys.argv[2]))))
