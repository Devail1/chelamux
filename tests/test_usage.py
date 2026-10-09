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


# ---------------------------------------------------------------------------
# Invariant guards (CMX-38 rework): each asserts the rule itself at its boundary,
# so a corruption of any single clause goes red — not only the example that
# happened to sit far from every threshold.
# ---------------------------------------------------------------------------

def _counts(requests, inp, cr, cw, out=0):
    return {"requests": requests, "in": inp, "cr": cr, "cw": cw, "out": out}


@pytest.mark.parametrize("c, broken", [
    # Every clause satisfied -> flagged.
    (_counts(10, 0, 400_000, 700_000), True),          # hit 36%, 10 req, 1.1M tokens
    # Each clause violated ALONE -> not flagged.
    (_counts(9, 0, 400_000, 700_000), False),          # one request short
    (_counts(10, 0, 300_000, 700_000), False),         # exactly 1M tokens: not MORE than
    (_counts(500, 0, 1_000, 99_000), False),           # 1% hit, many requests, only 100k tokens
    (_counts(10, 0, 550_000, 550_000), False),         # exactly 50% hit: not UNDER
    (_counts(10, 0, 600_000, 500_000), False),         # 55% hit
    (_counts(10, 0, 0, 0), False),                     # no prompt tokens: hit unknown
])
def test_cache_broken_each_clause_is_load_bearing(c, broken):
    assert usage.is_cache_broken(c) is broken


def test_small_low_hit_session_is_not_flagged_end_to_end(tmp_path):
    # Many requests at ~0% hit, but tiny: a quick script, not a broken cache draining the limit.
    lines = [_line(NOW - 1500 + i * 30, f"m{i}", f"r{i}", inp=50, cw=2_000, cr=0, out=10)
             for i in range(40)]
    _write(tmp_path, "p/small.jsonl", lines)
    (row,) = _rows(tmp_path, NOW - 1800)
    assert row["requests"] == 40 and row["cache_hit"] == 0
    assert row["cache_broken"] is False


def test_ten_requests_is_enough_to_flag(tmp_path):
    _write(tmp_path, "p/bot.jsonl", _bot_lines(n=10))
    (row,) = _rows(tmp_path, NOW - 1800)
    assert row["cache_broken"] is True


def test_dedupe_spans_sessions_but_idless_records_each_count(tmp_path):
    shared = _line(NOW - 120, "msg_x", "req_x", cr=1_000)
    _write(tmp_path, "p/a.jsonl", [shared])
    _write(tmp_path, "p/b.jsonl", [shared, _line(NOW - 60, "msg_y", "req_y", cr=10)])
    idless = json.loads(_line(NOW - 90, None, None, cr=7))
    _write(tmp_path, "p/c.jsonl", [json.dumps(idless)] * 3)
    rows = _rows(tmp_path, NOW - 1800)
    assert sum(r["requests"] for r in rows) == 1 + 1 + 3
    assert sum(r["cache_read"] for r in rows) == 1_000 + 10 + 3 * 7


def test_same_message_with_another_request_id_is_a_separate_request(tmp_path):
    _write(tmp_path, "p/s.jsonl", [_line(NOW - 120, "msg", "r1"), _line(NOW - 60, "msg", "r2")])
    (row,) = _rows(tmp_path, NOW - 1800)
    assert row["requests"] == 2


def test_rows_sorted_by_weighted_desc_and_window_bounds_inclusive(tmp_path):
    _write(tmp_path, "p/mid.jsonl", [_line(NOW - 1800, "a", "a", out=200)])       # at since
    _write(tmp_path, "p/big.jsonl", [_line(NOW, "b", "b", out=900)])              # at until
    _write(tmp_path, "p/small.jsonl", [_line(NOW - 60, "c", "c", out=10)])
    _write(tmp_path, "p/out.jsonl", [_line(NOW - 1801, "d", "d", out=99_999)])    # just before
    rows = _rows(tmp_path, NOW - 1800)
    ws = [r["weighted"] for r in rows]
    assert ws == sorted(ws, reverse=True) and len(rows) == 3
    assert [r["output"] for r in rows] == [900, 200, 10]


def test_sparkline_buckets_by_time(tmp_path):
    _write(tmp_path, "p/s.jsonl", [_line(NOW - 1800, "a", "a", inp=0, out=1),
                                   _line(NOW, "b", "b", inp=0, out=2)])
    (row,) = _rows(tmp_path, NOW - 1800)
    assert len(row["spark"]) == 15
    assert row["spark"][0] == 5 and row["spark"][-1] == 10 and sum(row["spark"]) == 15


def test_cache_hit_rate_formula(tmp_path):
    _write(tmp_path, "p/s.jsonl", [_line(NOW - 60, "a", "a", inp=100, cw=300, cr=600)])
    (row,) = _rows(tmp_path, NOW - 1800)
    assert row["cache_hit"] == pytest.approx(600 / 1000)


def test_label_rule():
    row = {"session_id": "abcdef12-3456", "project": "proj", "subagent": None}
    assert usage.label(row, {"abcdef12-3456": "my-window"}) == "my-window"
    assert usage.label(row, {"other": "x"}) == "proj · abcdef12"


def test_partial_line_is_read_once_complete(tmp_path):
    p = _write(tmp_path, "p/s.jsonl", [_line(NOW - 120, "m1", "r1")])
    half = _line(NOW - 60, "m2", "r2")
    with p.open("a") as fh:
        fh.write(half[:40])
    os.utime(p, (NOW, NOW))
    assert _rows(tmp_path, NOW - 1800)[0]["requests"] == 1
    with p.open("a") as fh:
        fh.write(half[40:] + "\n")
    os.utime(p, (NOW, NOW))
    assert _rows(tmp_path, NOW - 1800)[0]["requests"] == 2


def test_parse_ts_rejects_garbage():
    assert usage.parse_ts("2026-10-09") is None
    assert usage.parse_ts("not-a-timestamp-at-all") is None
    assert usage.parse_ts(None) is None


def test_limit_reset_since_newest_sample_is_unknown(tmp_path):
    ctx = tmp_path / "ctx"
    # A fresh (5 min old) sample whose 5h window reset 1 min ago: its 97% is the OLD window.
    _cache(ctx, "a.json", NOW - 300, five=(97, NOW - 60), seven=(30, NOW + 86400))
    lim = usage.limits(ctx, now=NOW, history=False)
    five = lim["five_hour"]
    assert five["used_pct"] is None and five["resets_at"] is None
    assert five["hits_100_before_reset"] is None and five["burn_pct_per_h"] is None
    assert "reset" in five["reason"]
    assert five["sampled_at"] == NOW - 300
    # The 7d window has not reset: still a reading.
    assert lim["seven_day"]["used_pct"] == 30


def test_limit_reset_exactly_now_is_unknown(tmp_path):
    ctx = tmp_path / "ctx"
    _cache(ctx, "a.json", NOW - 60, five=(50, NOW))
    assert usage.limits(ctx, now=NOW, history=False)["five_hour"]["used_pct"] is None


def test_limit_stale_boundary(tmp_path):
    ctx = tmp_path / "ctx"
    _cache(ctx, "a.json", NOW - usage.LIMIT_STALE_S, five=(42, NOW + 3600))
    assert usage.limits(ctx, now=NOW, history=False)["five_hour"]["used_pct"] == 42
    usage._SAMPLES.clear()
    _cache(ctx, "a.json", NOW - usage.LIMIT_STALE_S - 1, five=(42, NOW + 3600))
    assert usage.limits(ctx, now=NOW, history=False)["five_hour"]["used_pct"] is None


def test_limit_zero_percent_is_a_reading_not_unknown(tmp_path):
    ctx = tmp_path / "ctx"
    _cache(ctx, "a.json", NOW - 60, five=(0, NOW + 3600))
    five = usage.limits(ctx, now=NOW, history=False)["five_hour"]
    assert five["used_pct"] == 0 and five["reason"] is None


def test_limit_bool_pct_is_ignored(tmp_path):
    ctx = tmp_path / "ctx"
    _cache(ctx, "a.json", NOW - 60, five=(True, NOW + 3600))
    assert usage.limits(ctx, now=NOW, history=False)["five_hour"]["used_pct"] is None


def test_projection_below_100_is_reported_false(tmp_path):
    ctx = tmp_path / "ctx"
    reset = NOW + 3600
    _cache(ctx, "a.json", NOW - 20 * 60, five=(10, reset))
    usage.limits(ctx, now=NOW - 20 * 60, history=False)
    _cache(ctx, "a.json", NOW, five=(12, reset))
    five = usage.limits(ctx, now=NOW, history=False)["five_hour"]
    # 2 points in 20 min = 6 %/h; 12 + 6 * 1h = 18.
    assert five["burn_pct_per_h"] == pytest.approx(6.0)
    assert five["projected_pct"] == pytest.approx(18.0)
    assert five["hits_100_before_reset"] is False


def test_negative_burn_never_projects_below_current(tmp_path):
    ctx = tmp_path / "ctx"
    reset = NOW + 3600
    _cache(ctx, "a.json", NOW - 20 * 60, five=(30, reset))
    usage.limits(ctx, now=NOW - 20 * 60, history=False)
    _cache(ctx, "a.json", NOW, five=(20, reset))
    five = usage.limits(ctx, now=NOW, history=False)["five_hour"]
    assert five["burn_pct_per_h"] < 0
    assert five["projected_pct"] == 20
    assert five["hits_100_before_reset"] is False


def test_burn_needs_min_gap_and_takes_the_newest_eligible_sample():
    reset = NOW + 3600
    cur = (NOW, 50.0, reset)
    too_close = (NOW - usage.BURN_MIN_GAP_S + 1, 0.0, reset)
    assert usage.burn_rate([too_close], cur) is None
    older = (NOW - 3600, 10.0, reset)
    newer = (NOW - usage.BURN_MIN_GAP_S, 45.0, reset)
    assert usage.burn_rate([older, newer, too_close], cur) == pytest.approx(5.0 / (usage.BURN_MIN_GAP_S / 3600))
    jitter = (NOW - 3600, 10.0, reset + usage.RESET_TOLERANCE_S)
    assert usage.burn_rate([jitter], cur) == pytest.approx(40.0)
    assert usage.burn_rate([(NOW - 3600, 10.0, reset + usage.RESET_TOLERANCE_S + 1)], cur) is None


def test_api_usage_labels_chela_windows_by_live_window_name(tmp_path, monkeypatch):
    # Wiring: the dashboard's live {session_id: window name} map must reach usage.report.
    sid = "ffffffff-1234-5678-9abc-def012345678"
    root = tmp_path / "projects"
    _write(root, f"-work-chelamux/{sid}.jsonl", _healthy_lines(n=3, start=int(time.time()) - 300))
    _write(root, "-work-other/99999999-0000.jsonl",
           [_line(int(time.time()) - 60, "o1", "o1", cwd="/x/other")])
    for p in root.rglob("*.jsonl"):
        os.utime(p, None)
    monkeypatch.setattr(usage, "default_root", lambda: root)
    monkeypatch.setattr(usage, "extra_roots", lambda: [])
    monkeypatch.setattr(usage, "CONTEXT_CACHE_DIR", tmp_path / "ctx")
    monkeypatch.setattr(dash, "_usage_window_names", lambda: {sid: "chelamux-dev"})
    body = dash.app.test_client().get("/api/usage").get_json()
    rows = {r["session_id"]: r for r in body["windows"]["today"]["rows"]}
    assert rows[sid]["label"] == "chelamux-dev"
    assert rows[sid]["chela_window"] == "chelamux-dev"
    assert rows["99999999-0000"]["label"] == "other · 99999999"
    assert rows["99999999-0000"]["chela_window"] is None


def test_usage_window_names_maps_session_to_window(monkeypatch):
    monkeypatch.setattr(dash.discovery, "get_all_windows", lambda: {"alpha": "@1", "beta": "@2", "gamma": "@3"})
    sids = {"@1": "sid-a", "@2": None, "@3": "sid-c"}
    monkeypatch.setattr(dash.context, "window_session", lambda wid: sids[wid])
    assert dash._usage_window_names() == {"sid-a": "alpha", "sid-c": "gamma"}


def test_report_cache_ttl(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    p = _write(root, "p/s.jsonl", [_line(NOW - 60, "a", "a")])
    monkeypatch.setattr(usage, "default_root", lambda: root)
    monkeypatch.setattr(usage, "CONTEXT_CACHE_DIR", tmp_path / "ctx")
    first = usage.report({}, now=NOW, roots=[])
    assert first["windows"]["30m"]["rows"][0]["requests"] == 1
    with p.open("a") as fh:
        fh.write(_line(NOW - 30, "b", "b") + "\n")
    os.utime(p, (NOW, NOW))
    cached = usage.report({}, now=NOW + usage.RESULT_TTL_S - 1, roots=[])
    assert cached["windows"]["30m"]["rows"][0]["requests"] == 1
    fresh = usage.report({}, now=NOW + usage.RESULT_TTL_S, roots=[])
    assert fresh["windows"]["30m"]["rows"][0]["requests"] == 2
