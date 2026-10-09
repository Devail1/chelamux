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


def _live_transcripts(tmp_path):
    """The default root holds a healthy session; a Windows-side root holds the 10-08 bot."""
    root, win = tmp_path / "projects", tmp_path / "win"
    _write(root, "p/s.jsonl", _healthy_lines(n=3, start=int(time.time()) - 300))
    _write(win / "u1" / "projects", "q/bot.jsonl", _bot_lines(n=12, start=int(time.time()) - 900))
    for p in tmp_path.rglob("*.jsonl"):
        os.utime(p, None)
    return root, win


def test_api_usage_reads_roots_from_config_and_has_no_write_route(tmp_path, monkeypatch):
    # The extra roots are a plain config setting (CMX-38 scope cut): /api/usage reads them
    # from ~/.chela/config.json and echoes them read-only; there is no route that writes them.
    from chela import userconfig
    root, win = _live_transcripts(tmp_path)
    monkeypatch.delenv(usage.EXTRA_ROOTS_ENV, raising=False)
    monkeypatch.setattr(usage, "default_root", lambda: root)
    monkeypatch.setattr(usage, "CONTEXT_CACHE_DIR", tmp_path / "ctx")
    monkeypatch.setattr(dash, "_usage_window_names", lambda: {})
    userconfig.set_(usage.EXTRA_ROOTS_KEY, [str(win / "*" / "projects")])
    client = dash.app.test_client()

    body = client.get("/api/usage").get_json()
    rows = body["windows"]["30m"]["rows"]
    assert rows[0]["cache_broken"] is True and rows[0]["label"].startswith("open-mmo")
    assert body["roots"]["extra"] == [str(win / "*" / "projects")]
    assert body["roots"]["source"] == "config"
    assert body["roots"]["scanned"] == [str(root), str(win / "u1" / "projects")]
    assert body["limits"]["five_hour"]["used_pct"] is None

    assert client.post("/api/usage/roots", json={"roots": []}).status_code in (404, 405)
    assert userconfig.get(usage.EXTRA_ROOTS_KEY) == [str(win / "*" / "projects")]


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


# ---------------------------------------------------------------------------
# CMX-38 rework round 2: the production paths the first guards skipped — the
# context_snapshots burn history, the report cache's roots key, and the
# empty-vs-unset extra roots setting. Each is driven through report() / the HTTP
# route the dashboard actually calls, never through a test-only argument.
# ---------------------------------------------------------------------------

def _snapshot_db(tmp_path, monkeypatch, rows):
    """A real context_snapshots table (the daemon's), seeded with ``rows`` of
    ``(t, five_pct, five_reset, seven_pct, seven_reset)``."""
    from contextlib import closing
    from datetime import datetime, timezone

    from chela import context
    d = tmp_path / "chela-db"
    monkeypatch.setattr(context, "CHELA_DIR", d)
    monkeypatch.setattr(context, "DB_PATH", d / "scheduler.db")
    with closing(context._get_db()) as conn:
        for t, fp, fr, sp, sr in rows:
            conn.execute(
                "INSERT INTO context_snapshots (agent, ts, rate_limit_pct, rate_limit_resets_at, "
                "weekly_rl_pct, weekly_rl_resets_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("a", datetime.fromtimestamp(t, timezone.utc).isoformat(), fp, fr, sp, sr))
        conn.commit()


def test_report_burn_rate_comes_from_snapshot_history_after_a_restart(tmp_path, monkeypatch):
    # A freshly started dashboard: the in-process ring is EMPTY (the autouse fixture
    # cleared it) and has never seen an older sample. Only the daemon's
    # context_snapshots history can give a rate — and report() must use it.
    five_reset, seven_reset = NOW + 2 * 3600, NOW + 3 * 86400
    _snapshot_db(tmp_path, monkeypatch, [(NOW - 20 * 60, 10.0, five_reset, 2.0, seven_reset)])
    ctx = tmp_path / "ctx"
    _cache(ctx, "a.json", NOW - 60, five=(40, five_reset), seven=(5, seven_reset))
    monkeypatch.setattr(usage, "CONTEXT_CACHE_DIR", ctx)
    monkeypatch.setattr(usage, "default_root", lambda: tmp_path / "projects")
    assert usage._SAMPLES == []
    lim = usage.report({}, now=NOW, roots=[])["limits"]
    # 30 points in 19 min; and the 7d bar reads the WEEKLY columns, not the 5h ones.
    assert lim["five_hour"]["burn_pct_per_h"] == pytest.approx(30 / (19 / 60), rel=1e-3)
    assert lim["five_hour"]["hits_100_before_reset"] is True
    assert lim["seven_day"]["burn_pct_per_h"] == pytest.approx(3 / (19 / 60), rel=1e-2)


def test_report_without_snapshot_history_has_unknown_burn(tmp_path, monkeypatch):
    # The control for the test above: same cache file, empty history -> unknown, so the
    # rate there really came from the DB rows.
    _snapshot_db(tmp_path, monkeypatch, [])
    ctx = tmp_path / "ctx"
    _cache(ctx, "a.json", NOW - 60, five=(40, NOW + 7200))
    monkeypatch.setattr(usage, "CONTEXT_CACHE_DIR", ctx)
    monkeypatch.setattr(usage, "default_root", lambda: tmp_path / "projects")
    assert usage.report({}, now=NOW, roots=[])["limits"]["five_hour"]["burn_pct_per_h"] is None


def test_report_cache_is_keyed_on_the_scanned_roots(tmp_path, monkeypatch):
    root, extra = tmp_path / "projects", tmp_path / "win" / "projects"
    root.mkdir()
    _write(extra, "q/bot.jsonl", _bot_lines(n=12))
    monkeypatch.setattr(usage, "default_root", lambda: root)
    monkeypatch.setattr(usage, "CONTEXT_CACHE_DIR", tmp_path / "ctx")
    with_extra = usage.report({}, now=NOW, roots=[str(extra)])
    assert with_extra["windows"]["30m"]["rows"][0]["cache_broken"] is True
    # Same instant — well inside the TTL — but other roots: must rescan, not serve the
    # previous roots' rows.
    without = usage.report({}, now=NOW, roots=[])
    assert without["windows"]["30m"]["rows"] == []
    assert without["roots"]["scanned"] == [str(root)]
    # And back again, still inside the TTL.
    assert usage.report({}, now=NOW + 1, roots=[str(extra)])["windows"]["30m"]["rows"]


@pytest.mark.parametrize("stored, expected", [
    (None, "DEFAULT"),       # never set -> the default /mnt/c root
    ([], []),                # explicitly empty list -> scan nothing extra
    ("", []),                # an empty string (hand-edited config) -> nothing extra
    ("[]", []),              # the JSON spelling of the same
    (["/x/*"], ["/x/*"]),
    ([" ", "/y"], ["/y"]),
    ("/a, /b", ["/a", "/b"]),
    (["relative"], "DEFAULT"),  # invalid -> treated as absent, like any dashboard_setting
])
def test_extra_roots_empty_is_none_unset_is_default(monkeypatch, stored, expected):
    import json as _json

    from chela import userconfig
    monkeypatch.delenv(usage.EXTRA_ROOTS_ENV, raising=False)
    if stored is not None:
        # Written straight to the file: userconfig.set_ drops "", and a hand edit must not.
        userconfig._PATH.parent.mkdir(parents=True, exist_ok=True)
        userconfig._PATH.write_text(_json.dumps({usage.EXTRA_ROOTS_KEY: stored}))
    want = list(usage.DEFAULT_EXTRA_ROOTS) if expected == "DEFAULT" else expected
    assert usage.extra_roots() == want
    assert usage.extra_roots_setting()[1] == ("default" if expected == "DEFAULT" else "config")


@pytest.mark.parametrize("env, stored, expected, source", [
    ("/e/*", ["/c"], ["/e/*"], "env"),         # env beats config.json
    ("[]", ["/c"], [], "env"),                 # env "[]" turns the extra roots off
    ("", ["/c"], ["/c"], "config"),            # an empty env var is UNSET, as for every knob
    ("rel/x", ["/c"], ["/c"], "config"),       # a bad env value falls through
    (None, None, "DEFAULT", "default"),
])
def test_extra_roots_env_precedence(monkeypatch, env, stored, expected, source):
    from chela import userconfig
    if env is None:
        monkeypatch.delenv(usage.EXTRA_ROOTS_ENV, raising=False)
    else:
        monkeypatch.setenv(usage.EXTRA_ROOTS_ENV, env)
    if stored is not None:
        userconfig.set_(usage.EXTRA_ROOTS_KEY, stored)
    want = list(usage.DEFAULT_EXTRA_ROOTS) if expected == "DEFAULT" else expected
    assert usage.extra_roots_setting() == (want, source)


def test_api_empty_roots_setting_scans_no_extra_root_and_unset_restores_default(tmp_path, monkeypatch):
    # End to end through the route the Usage view calls, with the DEFAULT extra root
    # pointed at a real directory holding the 10-08 bot — so "fell back to the default"
    # is visible as a row — and no _REPORT.clear() between config changes: a change of
    # roots inside the cache TTL must rescan (the cache is keyed on the scanned roots).
    import json as _json

    from chela import userconfig
    root, win = _live_transcripts(tmp_path)
    monkeypatch.delenv(usage.EXTRA_ROOTS_ENV, raising=False)
    monkeypatch.setattr(usage, "default_root", lambda: root)
    monkeypatch.setattr(usage, "DEFAULT_EXTRA_ROOTS", (str(win / "*" / "projects"),))
    monkeypatch.setattr(usage, "CONTEXT_CACHE_DIR", tmp_path / "ctx")
    monkeypatch.setattr(dash, "_usage_window_names", lambda: {})
    client = dash.app.test_client()

    def get():
        body = client.get("/api/usage").get_json()
        return [r for r in body["windows"]["30m"]["rows"] if r["cache_broken"]], body["roots"]

    broken, roots = get()
    assert len(broken) == 1 and roots["source"] == "default"      # unset -> default scanned
    assert roots["scanned"] == [str(root), str(win / "u1" / "projects")]
    userconfig._PATH.parent.mkdir(parents=True, exist_ok=True)
    for empty in ([], "", "[]"):
        userconfig._PATH.write_text(_json.dumps({usage.EXTRA_ROOTS_KEY: empty}))
        broken, roots = get()
        assert broken == [], f"{empty!r} still scanned an extra root"
        assert roots["extra"] == [] and roots["scanned"] == [str(root)]
    userconfig._PATH.write_text("{}")
    broken, roots = get()
    assert len(broken) == 1                                        # unset again -> default


def test_api_env_empty_roots_scans_no_extra_root(tmp_path, monkeypatch):
    root, win = _live_transcripts(tmp_path)
    monkeypatch.setattr(usage, "default_root", lambda: root)
    monkeypatch.setattr(usage, "DEFAULT_EXTRA_ROOTS", (str(win / "*" / "projects"),))
    monkeypatch.setattr(usage, "CONTEXT_CACHE_DIR", tmp_path / "ctx")
    monkeypatch.setattr(dash, "_usage_window_names", lambda: {})
    monkeypatch.setenv(usage.EXTRA_ROOTS_ENV, "[]")
    body = dash.app.test_client().get("/api/usage").get_json()
    assert body["roots"] == {"default": str(root), "extra": [], "source": "env", "scanned": [str(root)]}
    assert all(not r["cache_broken"] for r in body["windows"]["30m"]["rows"])


def test_api_usage_labels_by_window_name_through_live_discovery(tmp_path, monkeypatch):
    # Guard 1 on the production path, nothing in app.py stubbed: discovery's live
    # windows + context's per-window session id -> the row's label is the window name.
    sid = "abcdef01-1234-5678-9abc-def012345678"
    root = tmp_path / "projects"
    _write(root, f"-work-chelamux/{sid}.jsonl", _healthy_lines(n=3, start=int(time.time()) - 300))
    _write(root, "-work-other/99999999-0000.jsonl", [_line(int(time.time()) - 60, "o1", "o1", cwd="/x/other")])
    for p in root.rglob("*.jsonl"):
        os.utime(p, None)
    monkeypatch.setattr(usage, "default_root", lambda: root)
    monkeypatch.setenv(usage.EXTRA_ROOTS_ENV, "[]")
    monkeypatch.setattr(usage, "CONTEXT_CACHE_DIR", tmp_path / "ctx")
    monkeypatch.setattr(dash.discovery, "get_all_windows", lambda: {"review-west": "@7", "idle": "@8"})
    monkeypatch.setattr(dash.context, "window_session", {"@7": sid, "@8": None}.get)
    rows = {r["session_id"]: r for r in dash.app.test_client().get("/api/usage").get_json()["windows"]["today"]["rows"]}
    assert rows[sid]["label"] == "review-west" and rows[sid]["chela_window"] == "review-west"
    assert rows["99999999-0000"]["label"] == "other · 99999999"


def test_api_usage_limit_reset_since_newest_sample_reads_unknown(tmp_path, monkeypatch):
    # Guard 2 through /api/usage (history on, as production): a FRESH sample whose 5h
    # window has already reset must not show its 97% — and the 7d bar, same file, must.
    now = time.time()
    ctx = tmp_path / "ctx"
    _cache(ctx, "a.json", now - 120, five=(97, int(now) - 30), seven=(30, int(now) + 86400))
    _snapshot_db(tmp_path, monkeypatch, [(now - 1800, 90.0, int(now) - 30, 28.0, int(now) + 86400)])
    monkeypatch.setattr(usage, "CONTEXT_CACHE_DIR", ctx)
    monkeypatch.setattr(usage, "default_root", lambda: tmp_path / "projects")
    monkeypatch.setenv(usage.EXTRA_ROOTS_ENV, "[]")
    monkeypatch.setattr(dash, "_usage_window_names", lambda: {})
    lim = dash.app.test_client().get("/api/usage").get_json()["limits"]
    five = lim["five_hour"]
    assert five["used_pct"] is None and five["burn_pct_per_h"] is None
    assert five["hits_100_before_reset"] is None and five["projected_pct"] is None
    assert "reset" in five["reason"]
    assert lim["seven_day"]["used_pct"] == 30


# ---------------------------------------------------------------------------
# Per-file reader state and row facts — each rule asserted on its own value.
# ---------------------------------------------------------------------------

def test_rewritten_shorter_transcript_is_reparsed_from_scratch(tmp_path):
    _write(tmp_path, "p/s.jsonl", [_line(NOW - 120, f"m{i}", f"r{i}") for i in range(3)])
    assert _rows(tmp_path, NOW - 1800)[0]["requests"] == 3
    _write(tmp_path, "p/s.jsonl", [_line(NOW - 60, "n0", "x0")])  # shrank: a rewrite
    rows = _rows(tmp_path, NOW - 1800)
    assert rows[0]["requests"] == 1 and rows[0]["last_ts"] == NOW - 60


def test_reader_drops_records_before_the_day_start_from_its_state(tmp_path):
    day0 = usage.utc_day_start(NOW)
    p = _write(tmp_path, "p/s.jsonl", [_line(day0 - 60, "y", "y"), _line(day0 + 60, "t", "t")])
    usage.collect([str(tmp_path)], day0)
    assert [r["t"] for r in usage._FILES[str(p)].records] == [day0 + 60]
    # A later scan with a later cutoff (day rollover) trims what was already held.
    usage.collect([str(tmp_path)], day0 + 120)
    assert usage._FILES[str(p)].records == []


def test_reader_forgets_files_no_longer_in_scope(tmp_path):
    p = _write(tmp_path, "p/s.jsonl", [_line(NOW - 60, "m", "r")])
    usage.collect([str(tmp_path)], usage.utc_day_start(NOW))
    assert str(p) in usage._FILES
    p.unlink()
    usage.collect([str(tmp_path)], usage.utc_day_start(NOW))
    assert str(p) not in usage._FILES


def test_row_carries_ai_title_majority_model_and_newest_ts(tmp_path):
    lines = [
        json.dumps({"type": "ai-title", "aiTitle": "Fix the cache"}),
        _line(NOW - 50, "a", "a", model="claude-sonnet-5-5"),
        _line(NOW - 300, "b", "b", model="claude-opus-5-5"),
        _line(NOW - 200, "c", "c", model="claude-opus-5-5"),
    ]
    _write(tmp_path, "p/s.jsonl", lines)
    row = _rows(tmp_path, NOW - 1800)[0]
    assert row["ai_title"] == "Fix the cache"
    assert row["model"] == "claude-opus-5-5"      # 2 of 3, though not the first seen
    assert row["last_ts"] == NOW - 50             # newest, though not the last line


def test_negative_or_non_int_token_counts_read_as_zero():
    rec = usage.parse_line(json.dumps({
        "type": "assistant", "timestamp": _iso(NOW), "requestId": "r",
        "message": {"id": "m", "model": "x", "usage": {
            "input_tokens": -5, "cache_creation_input_tokens": 2.5,
            "cache_read_input_tokens": "9", "output_tokens": 7}}}))
    assert (rec["in"], rec["cw"], rec["cr"], rec["out"]) == (0, 0, 0, 7)


def test_parse_ts_requires_a_full_timestamp():
    assert usage.parse_ts("2026-10-09T12:00:00Z") == NOW
    assert usage.parse_ts("2026-10-09T12:00") is None


def test_resolve_roots_dedupes_and_skips_non_dirs(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    (tmp_path / "f").write_text("x")
    (tmp_path / "link").symlink_to(a)
    assert usage.resolve_roots([str(a), str(a), str(tmp_path / "link"), str(tmp_path / "f")]) == [str(a)]


def test_top_n_caps_each_window(tmp_path, monkeypatch):
    assert usage.TOP_N == 25
    root = tmp_path / "projects"
    for i in range(usage.TOP_N + 3):
        _write(root, f"p/s{i}.jsonl", [_line(NOW - 60, f"m{i}", f"r{i}")])
    monkeypatch.setattr(usage, "default_root", lambda: root)
    monkeypatch.setattr(usage, "CONTEXT_CACHE_DIR", tmp_path / "ctx")
    w = usage.report({}, now=NOW, roots=[])["windows"]
    assert len(w["30m"]["rows"]) == usage.TOP_N and len(w["today"]["rows"]) == usage.TOP_N


def test_sample_ring_records_each_reading_once_and_is_bounded(tmp_path, monkeypatch):
    ctx = tmp_path / "ctx"
    _cache(ctx, "a.json", NOW - 60, five=(40, NOW + 7200))
    for _ in range(3):
        usage.limits(ctx, now=NOW, history=False)
    assert len(usage._SAMPLES) == 1
    monkeypatch.setattr(usage, "_SAMPLES_MAX", 2)
    for i in range(4):
        _cache(ctx, "a.json", NOW - 50 + i, five=(40 + i, NOW + 7200))
        usage.limits(ctx, now=NOW, history=False)
    assert len(usage._SAMPLES) == 2 and usage._SAMPLES[-1][2] == 43


def test_snapshot_history_older_than_eight_days_is_not_read(tmp_path, monkeypatch):
    reset = NOW + 3600
    _snapshot_db(tmp_path, monkeypatch, [(NOW - 9 * 86400, 1.0, reset, None, None),
                                         (NOW - 20 * 60, 10.0, reset, None, None)])
    got = usage._db_samples("five_hour", NOW - 8 * 86400)
    assert [p for _, p, _ in got] == [10.0]
