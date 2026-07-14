"""GET /api/log — the event log's READ endpoint, and the SSE `log` delta that
accelerates it.

The contracts, in the order the Feed depends on them:

  * ``/api/events`` is NOT this. That path was already taken by the SSE
    delta-notification stream (which carries no data), so the log is served
    somewhere else and there is exactly ONE reader behind it — ``event_log.read``,
    the same call ``chela events`` makes.
  * a CURSOR is honoured, and ``next_seq`` (never ``last_seq``) is what a client
    resumes from — a bounded read truncates, and ``last_seq`` would skip the rest.
  * a cursor that CANNOT be honoured comes back as a ``gap``, not as a
    plausible-looking wrong continuation.
  * the SSE stream pushes a ``log`` frame when the log's seq moves, carrying the
    seq — not the events. The client fetches those itself, from its own cursor.
"""

from __future__ import annotations

import pytest

from chela import event_log
from chela.dashboard import app as dash


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch):
    """Never the real ~/.chela/events.jsonl (the read cache is keyed by path)."""
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))


@pytest.fixture
def client():
    return dash.app.test_client()


def _seqs(payload):
    return [e["seq"] for e in payload["events"]]


def test_log_is_not_the_sse_path(client):
    """/api/events is the SSE stream; the log lives at /api/log. Two different things."""
    assert client.get("/api/log").status_code == 200
    # The SSE route still exists and is still a stream — this endpoint did not take it over.
    assert dash.app.view_functions["api_events"] is not dash.app.view_functions["api_log"]


def test_reads_the_log_the_cli_wrote(client):
    event_log.append("run_review", "cmx-58 awaiting review", {"pr": 71}, wid="@3")
    data = client.get("/api/log").get_json()

    assert _seqs(data) == [1]
    e = data["events"][0]
    assert e["type"] == "run_review"
    assert e["wid"] == "@3"
    assert e["summary"] == "cmx-58 awaiting review"
    assert e["payload"]["pr"] == 71
    assert data["boot_id"] == event_log.current_boot()
    assert data["gap"] is None


def test_cursor_returns_only_what_is_new(client):
    for i in range(3):
        event_log.append("tick", f"n={i}")
    first = client.get("/api/log").get_json()
    assert _seqs(first) == [1, 2, 3]

    event_log.append("tick", "n=3")
    resume = client.get(f"/api/log?after_seq={first['next_seq']}&after_boot={first['boot_id']}").get_json()
    assert _seqs(resume) == [4]
    assert resume["gap"] is None


def test_next_seq_not_last_seq_is_the_resume_point(client):
    for i in range(5):
        event_log.append("tick", f"n={i}")
    page = client.get("/api/log?limit=2").get_json()

    assert _seqs(page) == [1, 2]
    assert page["next_seq"] == 2      # where the caller has actually read to…
    assert page["last_seq"] == 5      # …NOT where the log is. Resuming from last_seq
    #                                    would silently skip 3, 4 and 5.
    rest = client.get(f"/api/log?after_seq={page['next_seq']}").get_json()
    assert _seqs(rest) == [3, 4, 5]


def test_a_stale_boot_is_reported_as_a_gap_not_silently_resumed(client):
    event_log.append("tick", "before")
    stale_boot = "deadbeefcafe"

    data = client.get(f"/api/log?after_seq=1&after_boot={stale_boot}").get_json()

    assert data["gap"] is not None
    assert data["gap"]["cursor_boot_id"] == stale_boot
    assert data["gap"]["boot_id"] == event_log.current_boot()
    assert "boot_id changed" in data["gap"]["reason"]


def test_a_cursor_ahead_of_the_log_is_a_gap(client):
    event_log.append("tick", "one")
    data = client.get("/api/log?after_seq=999").get_json()
    assert data["gap"] is not None
    assert "ahead of the log" in data["gap"]["reason"]


def test_filters_ride_through_to_the_one_reader(client):
    event_log.append("hook", "a", wid="@1")
    event_log.append("note", "b", wid="@2")
    event_log.append("hook", "c", wid="@2")

    by_type = client.get("/api/log?type=hook").get_json()
    assert [e["summary"] for e in by_type["events"]] == ["a", "c"]

    by_wid = client.get("/api/log?wid=@2").get_json()
    assert [e["summary"] for e in by_wid["events"]] == ["b", "c"]

    both = client.get("/api/log?type=hook&wid=@2").get_json()
    assert [e["summary"] for e in both["events"]] == ["c"]


def test_a_cursorless_read_is_bounded(client, monkeypatch):
    monkeypatch.setattr(dash, "LOG_DEFAULT_LIMIT", 3)
    for i in range(10):
        event_log.append("tick", f"n={i}")
    data = client.get("/api/log").get_json()
    assert len(data["events"]) == 3
    assert data["next_seq"] == 3          # …and it is resumable from there


# --- the SSE `log` delta -----------------------------------------------------

def test_sse_snapshot_moves_when_an_event_lands():
    before = dash._sse_log_snapshot()
    event_log.append("tick", "something happened")
    after = dash._sse_log_snapshot()

    assert after["seq"] == before["seq"] + 1
    assert after != before            # …which is exactly what makes the stream fire


def test_the_stream_pushes_a_log_frame_carrying_the_seq(monkeypatch):
    # Freeze everything else the loop diffs, so the only thing that can change is
    # the log. time.sleep is a no-op: the generator is pulled frame by frame here.
    monkeypatch.setattr(dash, "_sse_windows_snapshot", lambda: {})
    monkeypatch.setattr(dash, "_sse_runs_snapshot", lambda: {})
    monkeypatch.setattr(dash, "_sse_terms_snapshot", lambda: set())
    monkeypatch.setattr(dash.time, "sleep", lambda _s: None)

    stream = dash._sse_stream()
    assert next(stream).startswith("event: hello")   # the baseline, no log frame yet

    rec = event_log.append("run_review", "cmx-58 awaiting review")
    frame = next(stream)

    assert frame.startswith("event: log\n")
    assert f'"seq": {rec["seq"]}' in frame
    assert rec["boot_id"] in frame
    # A NOTIFICATION, not a payload: the summary is not in the frame — the client
    # fetches /api/log from its own cursor, so a dropped frame loses nothing.
    assert "awaiting review" not in frame
    stream.close()
