"""The Cost tab's Usage view — ``chela.usage`` and ``/api/usage`` (CMX-38).

Fixtures reproduce the 2026-10-08 incident's shape: a bot session whose
``cache_read`` is stuck at 11,707 tokens per call while ``cache_creation`` grows by
300-490k each call (~3% hit). It must rank first and be flagged; a healthy session
(high cache_read) must not be. Plus the counting rules (dedupe by
``(message.id, requestId)``, ``<synthetic>`` skipped), the UTC window, and the limit
bars' unknown-is-not-0% rule.
"""
from __future__ import annotations

import calendar
import json
import os
import time
from pathlib import Path

import pytest

from chela import usage
from chela.dashboard import app as dash

# 2026-10-09 12:00:00 UTC.
NOW = calendar.timegm((2026, 10, 9, 12, 0, 0))


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".123Z"


def _line(t, mid, rid, *, inp=3, cw=0, cr=0, out=100, model="claude-opus-5-5", cwd="/work/proj"):
    return json.dumps({
        "type": "assistant", "timestamp": _iso(t), "requestId": rid, "cwd": cwd,
        "message": {"id": mid, "model": model, "usage": {
            "input_tokens": inp, "cache_creation_input_tokens": cw,
            "cache_read_input_tokens": cr, "output_tokens": out}},
    })


def _write(root: Path, rel: str, lines: list[str]) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(x + "\n" for x in lines))
    os.utime(p, (NOW, NOW))
    return p


def _bot_lines(n=20, start=NOW - 1500, step=60):
    # The 10-08 shape: cache_read never moves off 11,707; cache_write grows each call.
    return [_line(start + i * step, f"msg_bot{i}", f"req_bot{i}",
                  inp=5, cw=300_000 + i * 9_000, cr=11_707, out=800, cwd="/win/open-mmo")
            for i in range(n)]


def _healthy_lines(n=20, start=NOW - 1500, step=60):
    return [_line(start + i * step, f"msg_ok{i}", f"req_ok{i}",
                  inp=4, cw=2_000, cr=180_000, out=1_500, cwd="/work/chelamux")
            for i in range(n)]


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    usage._FILES.clear()
    usage._REPORT.clear()
    usage._SAMPLES.clear()
    yield
    usage._FILES.clear()
    usage._REPORT.clear()
    usage._SAMPLES.clear()


def _rows(root: Path, since: int, until: int = NOW) -> list[dict]:
    return usage.aggregate(usage.collect([str(root)], usage.utc_day_start(NOW)), since, until, 15)


# ---------------------------------------------------------------------------
# The 10-08 incident
# ---------------------------------------------------------------------------

def test_10_08_bot_ranks_first_and_is_flagged_healthy_is_not(tmp_path):
    _write(tmp_path, "-win-open-mmo/bbbbbbbb-0000-0000-0000-000000000000.jsonl", _bot_lines())
    _write(tmp_path, "-work-chelamux/aaaaaaaa-0000-0000-0000-000000000000.jsonl", _healthy_lines())
    rows = _rows(tmp_path, NOW - 1800)
    assert [r["project"] for r in rows] == ["open-mmo", "chelamux"]
    bot, ok = rows
    assert bot["cache_broken"] is True
    assert bot["cache_hit"] < 0.05
    assert bot["cache_read"] == 20 * 11_707
    assert ok["cache_broken"] is False
    assert ok["cache_hit"] > 0.9


def test_low_hit_below_request_floor_is_not_flagged(tmp_path):
    # Same broken shape, but only 9 requests: under BROKEN_MIN_REQUESTS.
    _write(tmp_path, "p/cccccccc.jsonl", _bot_lines(n=9))
    (row,) = _rows(tmp_path, NOW - 1800)
    assert row["cache_hit"] < 0.05
    assert row["cache_broken"] is False


def test_weighted_total_formula(tmp_path):
    _write(tmp_path, "p/s.jsonl", [_line(NOW - 60, "m1", "r1", inp=10, cw=100, cr=1000, out=20)])
    (row,) = _rows(tmp_path, NOW - 1800)
    assert row["weighted"] == round(10 * 1 + 100 * 1.25 + 1000 * 0.1 + 20 * 5)
    assert (row["input"], row["cache_write"], row["cache_read"], row["output"]) == (10, 100, 1000, 20)


# ---------------------------------------------------------------------------
# Counting rules
# ---------------------------------------------------------------------------

def test_duplicate_message_request_pairs_count_once(tmp_path):
    # Claude Code writes one line per content block, each repeating the usage.
    line = _line(NOW - 120, "msg_dup", "req_dup", inp=7, cw=50, cr=500, out=40)
    _write(tmp_path, "p/s.jsonl", [line, line, line, _line(NOW - 60, "msg_2", "req_2")])
    (row,) = _rows(tmp_path, NOW - 1800)
    assert row["requests"] == 2
    assert row["cache_read"] == 500


def test_synthetic_rows_are_ignored(tmp_path):
    _write(tmp_path, "p/s.jsonl", [
        _line(NOW - 120, "m1", "r1", cr=900),
        _line(NOW - 60, "m2", "r2", cr=5_000_000, model="<synthetic>"),
    ])
    (row,) = _rows(tmp_path, NOW - 1800)
    assert row["requests"] == 1
    assert row["cache_read"] == 900


def test_window_uses_utc_timestamps(tmp_path, monkeypatch):
    # Under a non-UTC TZ a local-time parse would shift these records by hours, out
    # of the last-30-min window (or into the future of it).
    monkeypatch.setenv("TZ", "Asia/Jerusalem")
    time.tzset()
    try:
        _write(tmp_path, "p/s.jsonl", [_line(NOW - 300, "m1", "r1"), _line(NOW - 3600, "m0", "r0")])
        (row,) = _rows(tmp_path, NOW - 1800)
        assert row["requests"] == 1
        assert usage.parse_ts("2026-10-09T12:00:00.000Z") == NOW
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_today_is_the_utc_day_and_30m_is_a_subset(tmp_path):
    day0 = usage.utc_day_start(NOW)
    _write(tmp_path, "p/s.jsonl", [
        _line(day0 - 60, "y", "y"),           # yesterday (UTC)
        _line(day0 + 60, "a", "a"),           # today, hours ago
        _line(NOW - 60, "b", "b"),            # last 30 min
    ])
    sessions = usage.collect([str(tmp_path)], day0)
    (today,) = usage.aggregate(sessions, day0, NOW, 24)
    (recent,) = usage.aggregate(sessions, NOW - 1800, NOW, 15)
    assert today["requests"] == 2
    assert recent["requests"] == 1


def test_mtime_filter_skips_old_transcripts(tmp_path):
    p = _write(tmp_path, "p/old.jsonl", [_line(NOW - 60, "m", "r")])
    os.utime(p, (NOW - 2 * 86400, NOW - 2 * 86400))
    assert usage.collect([str(tmp_path)], usage.utc_day_start(NOW)) == []


def test_incremental_reread_picks_up_appended_lines(tmp_path):
    p = _write(tmp_path, "p/s.jsonl", [_line(NOW - 120, "m1", "r1")])
    assert _rows(tmp_path, NOW - 1800)[0]["requests"] == 1
    with p.open("a") as fh:
        fh.write(_line(NOW - 60, "m2", "r2") + "\n")
    os.utime(p, (NOW, NOW))
    assert _rows(tmp_path, NOW - 1800)[0]["requests"] == 2


def test_subagent_transcript_and_labels(tmp_path):
    sid = "dddddddd-1111-2222-3333-444444444444"
    _write(tmp_path, f"-work-chelamux/{sid}/subagents/agent-abc.jsonl", [_line(NOW - 60, "m", "r")])
    _write(tmp_path, "-work-other/eeeeeeee-0000.jsonl", [_line(NOW - 60, "m2", "r2", cwd="/x/other")])
    rows = {r["project"]: r for r in _rows(tmp_path, NOW - 1800)}
    sub = rows["proj"]
    assert sub["session_id"] == sid and sub["subagent"] == "agent-abc"
    assert usage.label(sub, {sid: "chelamux-dev"}) == "chelamux-dev › agent-abc"
    assert usage.label(rows["other"], {}) == "other · eeeeeeee"


# ---------------------------------------------------------------------------
# Limit bars
# ---------------------------------------------------------------------------

def _cache(dirpath: Path, name: str, mtime: float, five=None, seven=None):
    dirpath.mkdir(parents=True, exist_ok=True)
    rl = {}
    if five is not None:
        rl["five_hour"] = {"used_percentage": five[0], "resets_at": five[1]}
    if seven is not None:
        rl["seven_day"] = {"used_percentage": seven[0], "resets_at": seven[1]}
    p = dirpath / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"rate_limits": rl}))
    os.utime(p, (mtime, mtime))


def test_missing_rate_limits_is_unknown_not_zero(tmp_path):
    lim = usage.limits(tmp_path / "ctx", now=NOW, history=False)
    for key in ("five_hour", "seven_day"):
        assert lim[key]["used_pct"] is None
        assert lim[key]["reason"]


def test_stale_rate_limits_is_unknown_not_zero(tmp_path):
    ctx = tmp_path / "ctx"
    _cache(ctx, "a.json", NOW - 31 * 60, five=(42, NOW + 3600), seven=(10, NOW + 86400))
    lim = usage.limits(ctx, now=NOW, history=False)
    assert lim["five_hour"]["used_pct"] is None
    assert lim["seven_day"]["used_pct"] is None
    assert "min old" in lim["five_hour"]["reason"]


def test_freshest_file_wins_and_burn_projects_past_100(tmp_path):
    ctx = tmp_path / "ctx"
    reset = NOW + 2 * 3600
    _cache(ctx, "old.json", NOW - 20 * 60, five=(10, reset))
    usage.limits(ctx, now=NOW - 20 * 60, history=False)  # first sample seen
    _cache(ctx, "by-session/new.json", NOW - 60, five=(40, reset), seven=(5, NOW + 86400))
    lim = usage.limits(ctx, now=NOW, history=False)
    five = lim["five_hour"]
    assert five["used_pct"] == 40
    assert five["resets_at"] == reset
    # 30 points in 19 min ≈ 94.7 %/h; 40 + 94.7 * 2h ≫ 100.
    assert five["burn_pct_per_h"] == pytest.approx(30 / (19 / 60), rel=1e-3)
    assert five["hits_100_before_reset"] is True
    # The 7d bar has only one sample: a rate is unknown, not 0.
    assert lim["seven_day"]["used_pct"] == 5
    assert lim["seven_day"]["burn_pct_per_h"] is None


def test_burn_ignores_a_sample_from_a_previous_limit_window():
    cur = (NOW, 5.0, NOW + 4 * 3600)
    assert usage.burn_rate([(NOW - 1800, 90.0, NOW - 60)], cur) is None
    assert usage.burn_rate([(NOW - 1800, 1.0, NOW + 4 * 3600)], cur) == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# Roots setting + HTTP
# ---------------------------------------------------------------------------

def test_normalize_roots_rejects_relative():
    assert usage.normalize_roots("/a/*/x, /b\n") == ["/a/*/x", "/b"]
    with pytest.raises(ValueError):
        usage.normalize_roots(["relative/dir"])


def test_api_usage_and_roots(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    extra = tmp_path / "win" / "projects"
    _write(root, "p/s.jsonl", _healthy_lines(n=3, start=int(time.time()) - 300))
    _write(extra, "q/bot.jsonl", _bot_lines(n=12, start=int(time.time()) - 900))
    for p in list(root.rglob("*.jsonl")) + list(extra.rglob("*.jsonl")):
        os.utime(p, None)
    monkeypatch.setattr(usage, "default_root", lambda: root)
    monkeypatch.setattr(usage, "CONTEXT_CACHE_DIR", tmp_path / "ctx")
    monkeypatch.setattr(dash, "_usage_window_names", lambda: {})
    client = dash.app.test_client()

    r = client.post("/api/usage/roots", json={"roots": ["not/absolute"]})
    assert r.status_code == 400
    r = client.post("/api/usage/roots", json={"roots": [str(tmp_path / "w*" / "projects")]})
    assert r.status_code == 200 and r.get_json()["extra"] == [str(tmp_path / "w*" / "projects")]

    body = client.get("/api/usage").get_json()
    rows = body["windows"]["30m"]["rows"]
    assert rows[0]["cache_broken"] is True and rows[0]["label"].startswith("open-mmo")
    assert body["limits"]["five_hour"]["used_pct"] is None

    client.post("/api/usage/roots", json={"roots": []})
    usage._REPORT.clear()
    body = client.get("/api/usage").get_json()
    assert all(not r["cache_broken"] for r in body["windows"]["30m"]["rows"])
