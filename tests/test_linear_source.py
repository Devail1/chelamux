"""📐🔗 CMX-432 — the Linear tracker adapter (`chela/sources/linear.py`).

Every test stubs the GraphQL transport (`FakeLinear`) or the HTTP layer under the real
transport (`_urlopen_*`) — no test talks to Linear.
"""
from __future__ import annotations

import io
import json
import logging
import re
import subprocess
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import config, dispatcher, envutil, runtime_truth
from chela.sources import linear
from chela.sources.linear import LinearError, LinearSource

SECRET = "lin_api_SUPERSECRETvalue0123456789"


@pytest.fixture(autouse=True)
def _fresh_module_state(tmp_path, monkeypatch):
    linear._backoff.clear()
    linear._last_sweep.clear()
    linear._reported.clear()
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-state")
    yield
    linear._backoff.clear()
    linear._last_sweep.clear()
    linear._reported.clear()


def _wf(tmp_path, **tracker):
    cfg = {"project_key": "CMX", "tracker": {"kind": "linear", "team": "CMX", **tracker}}

    def get(*keys, default=None):
        cur = cfg
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    return SimpleNamespace(path=tmp_path / "WORKFLOW.md", get=get, config=cfg,
                           project_key="CMX")


_TYPES = {"Backlog": "backlog", "Todo": "unstarted", "In Progress": "started",
          "In Review": "started", "Done": "completed", "Canceled": "canceled"}


def issue(n, title=None, *, state="Todo", priority=0, sort=0.0, desc=None, blockers=(),
          archived=False, branch=None, team="CMX"):
    return {
        "id": f"uuid-{n}" if team == "CMX" else f"uuid-{team}-{n}", "identifier": f"{team}-{n}",
        "number": n,
        "title": title or f"task {n}", "description": desc, "priority": priority,
        "sortOrder": sort, "url": f"https://linear.app/x/issue/CMX-{n}",
        "branchName": branch if branch is not None else f"{team.lower()}-{n}-task-{n}",
        "archivedAt": "2026-10-01T00:00:00Z" if archived else None,
        "state": {"name": state, "type": _TYPES[state]},
        "labels": {"nodes": []},
        "inverseRelations": {"nodes": list(blockers)},
    }


def blocked_by(ident, state="Todo", *, unreadable=False):
    if unreadable:
        return {"type": "blocks", "issue": None}
    return {"type": "blocks",
            "issue": {"identifier": ident, "archivedAt": None,
                      "state": {"type": _TYPES[state]}}}


class FakeLinear:
    """An in-memory Linear team behind the transport seam. `fail` makes every call raise."""

    def __init__(self, issues=(), fail: LinearError | None = None):
        self.issues = {i["identifier"]: i for i in issues}
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []
        self.refuse_archive = False
        self.archive_success = True               # False: Linear's `success: false` on a 200
        self.refuse_update = False
        self.states = dict(_TYPES)               # the team's workflow states, name → type

    def _page(self, nodes):
        return {"issues": {"nodes": nodes, "pageInfo": {"hasNextPage": False,
                                                        "endCursor": None}}}

    def __call__(self, query, variables):
        name = {
            linear.OPEN_ISSUES_QUERY: "open", linear.ISSUES_BY_NUMBER_QUERY: "by_number",
            linear.TEAM_STATES_QUERY: "states", linear.SWEEP_QUERY: "sweep",
            linear.UPDATE_STATE_MUTATION: "update", linear.ARCHIVE_MUTATION: "archive",
        }[query]
        self.calls.append((name, variables))
        if self.fail is not None:
            raise self.fail
        nodes = [i for i in self.issues.values() if _matches(query, i, variables)]
        if name in ("by_number", "open", "sweep"):
            return self._page(nodes)
        if name == "states":
            return {"teams": {"nodes": [{"id": "team", "states": {"nodes": [
                {"id": f"st-{k}", "name": k, "type": t, "position": p}
                for p, (k, t) in enumerate(self.states.items())]}}]}}
        target = next(i for i in nodes if isinstance(i, dict) and i["id"] == variables["id"])
        if name == "update":
            if self.refuse_update:
                return {"issueUpdate": {"success": False}}
            sid = variables["stateId"][len("st-"):]
            target["state"] = {"name": sid, "type": self.states[sid]}
            return {"issueUpdate": {"success": True}}
        if self.refuse_archive:
            raise LinearError("graphql", "archive refused")
        if not self.archive_success:
            return {"issueArchive": {"success": False}}
        target["archivedAt"] = "2026-10-01T12:00:00Z"
        return {"issueArchive": {"success": True}}

    def names(self):
        return [n for n, _ in self.calls]


def _matches(query: str, node: dict, variables: dict) -> bool:
    """Apply the filter the QUERY TEXT asks for, the way Linear would — so a query whose
    filter is wrong returns the wrong issues here too, instead of the fake knowing better.
    The team filter and the number filter are read from the query too: a query that drops
    or inverts either one gets another team's issues (or the wrong numbers) back here.
    A malformed record (no dict, no state dict) passes through: the adapter must cope."""
    if not isinstance(node, dict):
        return True
    ident = node.get("identifier")
    node_team = ident.split("-")[0] if isinstance(ident, str) and "-" in ident else None
    team = re.search(r"team:\s*\{\s*key:\s*\{\s*(eq|neq):\s*\$team\s*\}", query)
    if team and node_team is not None:
        if (node_team == variables.get("team")) != (team.group(1) == "eq"):
            return False
    number = re.search(r"number:\s*\{\s*(in|nin):\s*\$numbers\s*\}", query)
    if number and isinstance(node.get("number"), int):
        if (node["number"] in variables.get("numbers", [])) != (number.group(1) == "in"):
            return False
    if not isinstance(node.get("state"), dict):
        return True
    archived = re.search(r"includeArchived:\s*(true|false)", query)
    if node["archivedAt"] and not (archived and archived.group(1) == "true"):
        return False
    nin = re.search(r"state:\s*\{\s*type:\s*\{\s*nin:\s*\[([^\]]*)\]", query)
    if nin and node["state"]["type"] in re.findall(r'"(\w+)"', nin.group(1)):
        return False
    return True


def _src(tmp_path, fake, **tracker):
    return LinearSource(_wf(tmp_path, **tracker), transport=fake)


# --- the read contract ---------------------------------------------------------------

def _urlopen_raising(exc):
    def urlopen(req, timeout=None):
        raise exc
    return urlopen


def _urlopen_returning(payload: bytes):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        return Resp(payload)
    return urlopen


def _http_error(code, body=b"{}"):
    return urllib.error.HTTPError(linear.LINEAR_API_URL, code, "err", {}, io.BytesIO(body))


FAILURES = {
    "network": _urlopen_raising(urllib.error.URLError("no route")),
    "401": _urlopen_raising(_http_error(401)),
    "429": _urlopen_raising(_http_error(429)),
    "ratelimited-400": _urlopen_raising(_http_error(
        400, json.dumps({"errors": [{"message": "slow down",
                                     "extensions": {"code": "RATELIMITED"}}]}).encode())),
    "not-json": _urlopen_returning(b"<html>gateway</html>"),
    "no-data": _urlopen_returning(b'{"data": null}'),
}


@pytest.mark.parametrize("failure", sorted(FAILURES))
def test_a_failed_read_makes_fetch_by_ids_return_none_never_empty(tmp_path, failure):
    """G1: absence is meaningful, so a read that FAILED must say so (None) — `[]` would
    read as "none of these are open any more" and close every live run."""
    src = _src(tmp_path, linear._http_transport(SECRET, urlopen=FAILURES[failure]))
    assert src.fetch_by_ids(["CMX-1", "CMX-2"]) is None
    assert src.list_open_tasks() == [] and src.read_failed is True


def test_a_malformed_requested_record_fails_the_whole_refresh(tmp_path):
    """SPEC 11.1: an ID refresh MUST fail rather than silently omit a requested record."""
    bad = issue(2)
    bad["state"] = None
    src = _src(tmp_path, FakeLinear([issue(1), bad]))
    assert src.fetch_by_ids(["CMX-1", "CMX-2"]) is None


def test_an_archived_issue_reads_as_done_not_missing(tmp_path):
    """Archiving is how the free plan's cap is kept — so an archived issue must come back
    from the ID refresh as TERMINAL (done), never as absent and never as a failed read.
    CMX-3 was archived straight out of Todo: `archivedAt` alone makes it terminal."""
    fake = FakeLinear([issue(1, state="Done", archived=True), issue(2, state="In Progress"),
                       issue(3, state="Todo", archived=True)])
    got = {t.id: t for t in _src(tmp_path, fake).fetch_by_ids(["CMX-1", "CMX-2", "CMX-3"])}
    assert got["CMX-1"].terminal_state == "done"
    assert got["CMX-3"].terminal_state == "done"
    assert got["CMX-2"].terminal_state is None
    # CMX-430 integration: the dispatcher reconciles on `state`, so a terminal issue must
    # read CLOSED there too (an archived one included), and a live one OPEN.
    assert (got["CMX-1"].state, got["CMX-3"].state, got["CMX-2"].state) == ("closed", "closed", "open")
    assert fake.calls[0][1]["numbers"] == [1, 2, 3]


def test_the_open_set_is_every_non_terminal_state_not_just_todo(tmp_path):
    """The GitHub integration moves an issue to In Progress / In Review on its own; if the
    open set were only `Todo`, that issue would read as ABSENT and its run as done."""
    fake = FakeLinear([issue(1, state="Todo"), issue(2, state="In Progress"),
                       issue(3, state="In Review"), issue(4, state="Backlog"),
                       issue(5, state="Done"), issue(6, state="Canceled")])
    src = _src(tmp_path, fake)
    assert {t.id for t in src.list_open_tasks()} == {"CMX-1", "CMX-2", "CMX-3", "CMX-4"}
    assert src.read_failed is False


def test_the_description_becomes_the_task_body(tmp_path):
    desc = "**Do.** the thing\n\n**GUARDS.** corrupt → RED"
    src = _src(tmp_path, FakeLinear([issue(7, desc=desc), issue(8, desc="  ")]))
    tasks = {t.id: t for t in src.list_open_tasks()}
    assert tasks["CMX-7"].body == desc
    assert tasks["CMX-8"].body is None
    vars_ = dispatcher._prompt_vars(_wf(tmp_path), tasks["CMX-7"], "/w", "b", "dev", 7)
    assert vars_["task_body"] == desc


def test_task_identity_number_and_branch_come_from_linear(tmp_path):
    t = _src(tmp_path, FakeLinear([issue(12, "Tighten top row",
                                         branch="cmx-12-tighten-top-row")])).list_open_tasks()[0]
    assert (t.id, t.task_number, t.branch) == ("CMX-12", 12, "cmx-12-tighten-top-row")
    # A suggested name that lost the identifier is rebuilt, never trusted: the identifier
    # is what Linear links on and what keeps it from ever equalling an old `cmx-12`.
    t = _src(tmp_path, FakeLinear([issue(12, "Tighten top row", branch="feature/x")]
                                  )).list_open_tasks()[0]
    assert t.branch == "cmx-12-tighten-top-row"


@pytest.mark.parametrize("suggested,expected", [
    # trusted: it carries THIS issue's identifier, as a whole token
    ("cmx-12-tighten-top-row", "cmx-12-tighten-top-row"),
    ("CMX-12-Tighten-Top-Row", "cmx-12-tighten-top-row"),
    ("liav/cmx-12-tighten-top-row", "liav/cmx-12-tighten-top-row"),
    # rebuilt: another issue's identifier that merely STARTS with this one's …
    ("cmx-123-other-work", "cmx-12-tighten-top-row"),
    ("cmx-1-other-work", "cmx-12-tighten-top-row"),
    # … the identifier glued inside a word, or not followed by the slug …
    ("xcmx-12-tighten", "cmx-12-tighten-top-row"),
    ("cmx-12", "cmx-12-tighten-top-row"),
    # … no identifier at all, or not a legal branch name
    ("tighten-top-row", "cmx-12-tighten-top-row"),
    ("cmx-12-a b", "cmx-12-tighten-top-row"),
    ("cmx-12-a..b", "cmx-12-tighten-top-row"),
    ("", "cmx-12-tighten-top-row"),
])
def test_a_branch_name_is_trusted_only_when_it_carries_the_identifier(
        tmp_path, suggested, expected):
    """CMX-432 rework: Linear's `branchName` is used only when it carries `cmx-12-` as a
    whole token — `cmx-123-…` is another issue's branch, and a bare substring match would
    adopt it. Anything else falls back to the derived `<identifier>-<title slug>`."""
    t = _src(tmp_path, FakeLinear([issue(12, "Tighten top row", branch=suggested)]
                                  )).list_open_tasks()[0]
    assert t.branch == expected


# --- claiming: ready state, blockers, order ------------------------------------------

def _claim(tmp_path, fake, **tracker):
    src = _src(tmp_path, fake, **tracker)
    return [t.id for t in dispatcher._claim_order(_wf(tmp_path), src, src.list_open_tasks())]


def test_only_the_ready_state_is_claimed(tmp_path):
    fake = FakeLinear([issue(1, state="Todo"), issue(2, state="In Progress"),
                       issue(3, state="Backlog")])
    assert _claim(tmp_path, fake) == ["CMX-1"]
    assert _claim(tmp_path, fake, ready_states=["Todo", "Backlog"]) == ["CMX-1", "CMX-3"]


def test_a_task_blocked_by_an_open_issue_is_held(tmp_path):
    fake = FakeLinear([issue(1, blockers=[blocked_by("CMX-2", "In Progress")]),
                       issue(2, state="In Progress"), issue(3)])
    assert _claim(tmp_path, fake) == ["CMX-3"]


def test_an_unreadable_blocker_holds_the_task(tmp_path):
    fake = FakeLinear([issue(1, blockers=[blocked_by("", unreadable=True)]), issue(3)])
    assert _claim(tmp_path, fake) == ["CMX-3"]


def test_a_canceled_blocker_still_holds_the_task(tmp_path):
    """Only `completed` satisfies a blocker — a canceled one is not the work being done."""
    fake = FakeLinear([issue(1, blockers=[blocked_by("CMX-9", "Canceled")])])
    assert _claim(tmp_path, fake) == []


def test_a_done_blocker_releases_the_task(tmp_path):
    fake = FakeLinear([issue(1, blockers=[blocked_by("CMX-9", "Done")])])
    assert _claim(tmp_path, fake) == ["CMX-1"]


def test_claim_order_is_priority_then_manual_order(tmp_path):
    """Urgent(1) → High(2) → Medium(3) → Low(4) → No priority(0) last; within a priority,
    Linear's `sortOrder` ascending (the board's manual order), not the issue number."""
    fake = FakeLinear([
        issue(1, priority=0, sort=-100.0),       # no priority: last, however high it sits
        issue(2, priority=3, sort=5.0),
        issue(3, priority=1, sort=50.0),
        issue(4, priority=3, sort=-5.0),
        issue(5, priority=2, sort=0.0),
    ])
    assert _claim(tmp_path, fake) == ["CMX-3", "CMX-5", "CMX-4", "CMX-2", "CMX-1"]


# --- closing + archiving -------------------------------------------------------------

def test_closing_a_task_marks_it_done_and_archives_it_once(tmp_path):
    fake = FakeLinear([issue(4, state="In Review")])
    src = _src(tmp_path, fake)
    assert src.close_tasks(["CMX-4"]) == {"CMX-4": "struck"}
    assert fake.names().count("archive") == 1
    assert fake.issues["CMX-4"]["state"]["type"] == "completed"
    assert fake.issues["CMX-4"]["archivedAt"]
    # Idempotent: a second close (next tick) writes nothing.
    fake.calls.clear()
    assert _src(tmp_path, fake).close_tasks(["CMX-4"]) == {"CMX-4": "already"}
    assert "archive" not in fake.names() and "update" not in fake.names()


def test_an_integration_closed_issue_is_archived_on_close(tmp_path):
    """Linear's GitHub integration set Done — chela must still archive it."""
    fake = FakeLinear([issue(4, state="Done")])
    assert _src(tmp_path, fake).close_tasks(["CMX-4"]) == {"CMX-4": "already"}
    assert fake.names().count("archive") == 1 and "update" not in fake.names()


def test_a_failed_archive_is_logged_and_never_raises(tmp_path, caplog):
    fake = FakeLinear([issue(4, state="Done")])
    fake.refuse_archive = True
    with caplog.at_level(logging.WARNING, logger="chela.sources.linear"):
        assert _src(tmp_path, fake).close_tasks(["CMX-4"]) == {"CMX-4": "already"}
    assert "could not archive CMX-4" in caplog.text


def test_done_state_selects_the_state_chela_sets_on_merge(tmp_path):
    """`tracker.done_state` names the state close_tasks moves the issue to — not the
    first `completed` state. Two completed states, so ignoring the setting is visible."""
    fake = FakeLinear([issue(4, state="In Review")])
    fake.states = {**_TYPES, "Shipped": "completed"}
    assert _src(tmp_path, fake, done_state="Shipped").close_tasks(["CMX-4"]) == {
        "CMX-4": "struck"}
    assert fake.issues["CMX-4"]["state"]["name"] == "Shipped"
    assert [v["stateId"] for n, v in fake.calls if n == "update"] == ["st-Shipped"]


def test_without_done_state_the_first_completed_state_is_set(tmp_path):
    fake = FakeLinear([issue(4, state="In Review")])
    fake.states = {**_TYPES, "Shipped": "completed"}
    assert _src(tmp_path, fake).close_tasks(["CMX-4"]) == {"CMX-4": "struck"}
    assert fake.issues["CMX-4"]["state"]["name"] == "Done"


def test_a_done_state_the_team_lacks_fails_the_close_and_writes_nothing(tmp_path):
    fake = FakeLinear([issue(4, state="In Review")])
    assert _src(tmp_path, fake, done_state="Shipped").close_tasks(["CMX-4"]) == {
        "CMX-4": "failed"}
    assert "update" not in fake.names() and "archive" not in fake.names()
    assert fake.issues["CMX-4"]["state"]["name"] == "In Review"


def test_a_refused_issue_update_is_failed_never_struck(tmp_path):
    """Linear answers a refused write with `success: false` on a 200 — that is `failed`
    (retried next tick), never `struck`, and the still-open issue is not archived."""
    fake = FakeLinear([issue(4, state="In Review")])
    fake.refuse_update = True
    assert _src(tmp_path, fake).close_tasks(["CMX-4"]) == {"CMX-4": "failed"}
    assert "archive" not in fake.names()
    assert fake.issues["CMX-4"]["archivedAt"] is None


def test_the_dispatcher_strike_closes_a_network_tracker(tmp_path):
    """`_strike_merged_tasks` reaches a tracker with `close_tasks` but no file."""
    fake = FakeLinear([issue(4, state="In Review")])
    assert dispatcher._strike_merged_tasks(_wf(tmp_path), _src(tmp_path, fake), ["CMX-4"]) == 1
    assert fake.names().count("archive") == 1


def test_the_sweep_archives_closed_issues_and_publishes_the_count(tmp_path):
    fake = FakeLinear([issue(1, state="Done"), issue(2, state="Canceled"),
                       issue(3, state="Todo"), issue(4, state="Done", archived=True)])
    src = _src(tmp_path, fake)
    assert src.archive_sweep() == 2
    assert linear.read_published_counts()["CMX"]["count"] == 1
    # Throttled: the next tick's sweep does nothing.
    fake.calls.clear()
    assert _src(tmp_path, fake).archive_sweep() is None and fake.calls == []


def test_a_sweep_archives_at_most_archives_per_sweep_issues(tmp_path):
    """A backlog clears over a few sweeps, never in one burst of mutations."""
    assert linear.ARCHIVES_PER_SWEEP == 50
    n = linear.ARCHIVES_PER_SWEEP + 3
    fake = FakeLinear([issue(i, state="Done") for i in range(1, n + 1)])
    assert _src(tmp_path, fake).archive_sweep() == linear.ARCHIVES_PER_SWEEP
    assert fake.names().count("archive") == linear.ARCHIVES_PER_SWEEP
    assert linear.read_published_counts()["CMX"]["count"] == 3
    # The next sweep takes the rest.
    assert _src(tmp_path, fake).archive_sweep(force=True) == 3
    assert fake.names().count("archive") == n


@pytest.mark.parametrize("count,warns", [(linear.ISSUE_COUNT_WARN_AT, False),
                                         (linear.ISSUE_COUNT_WARN_AT + 1, True)])
def test_the_doctor_fact_warns_above_200_non_archived_issues(tmp_path, count, warns):
    assert linear.ISSUE_COUNT_WARN_AT == 200
    linear._publish_count("CMX", count)
    found = runtime_truth._linear_issue_cap_report(
        {"CMX": "WORKFLOW.md"}, runtime_truth._linear_issue_cap_read())
    assert [f.level == runtime_truth.WARN for f in found] == [warns]


# --- rate limits ---------------------------------------------------------------------

def test_a_429_backs_off_without_calling_again(tmp_path):
    fake = FakeLinear([issue(1)], fail=LinearError("rate_limited", "HTTP 429"))
    assert _src(tmp_path, fake).fetch_by_ids(["CMX-1"]) is None
    fake.fail = None
    second = _src(tmp_path, fake)
    assert second.list_open_tasks() == [] and second.read_failed is True
    assert len(fake.calls) == 1                      # the back-off window held


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_back_off_grows_exponentially_with_consecutive_strikes(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(linear.time, "monotonic", clock)
    fake = FakeLinear([issue(1)], fail=LinearError("rate_limited", "HTTP 429"))
    waits = []
    for _ in range(6):
        assert _src(tmp_path, fake).fetch_by_ids(["CMX-1"]) is None
        retry_at, _strikes = linear._backoff["CMX"]
        waits.append(retry_at - clock.now)
        clock.now = retry_at + 1                     # the window passes; Linear says 429 again
    base = linear.BACKOFF_BASE_SECONDS
    assert waits == [base, 2 * base, 4 * base, 8 * base, linear.BACKOFF_MAX_SECONDS,
                     linear.BACKOFF_MAX_SECONDS]
    assert len(fake.calls) == 6
    # A good read resets the strikes: the next 429 waits the base again.
    fake.fail = None
    assert _src(tmp_path, fake).fetch_by_ids(["CMX-1"]) is not None
    assert "CMX" not in linear._backoff
    fake.fail = LinearError("rate_limited", "HTTP 429")
    _src(tmp_path, fake).fetch_by_ids(["CMX-1"])
    assert linear._backoff["CMX"][0] - clock.now == base


def test_back_off_honours_retry_after(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(linear.time, "monotonic", clock)
    err = urllib.error.HTTPError(linear.LINEAR_API_URL, 429, "err", {"Retry-After": "7"},
                                 io.BytesIO(b"{}"))
    src = _src(tmp_path, linear._http_transport(SECRET, urlopen=_urlopen_raising(err)))
    assert src.fetch_by_ids(["CMX-1"]) is None
    assert linear._backoff["CMX"][0] - clock.now == 7


_RATELIMITED = json.dumps({"errors": [{"message": "slow down",
                                       "extensions": {"code": "RATELIMITED"}}]}).encode()


def _urlopen_ratelimited_400(req, timeout=None):
    raise _http_error(400, _RATELIMITED)                   # fresh: its body is read once


@pytest.mark.parametrize("urlopen", [
    _urlopen_ratelimited_400,                              # how Linear actually sends it
    _urlopen_returning(_RATELIMITED),                      # the same error on a 200
], ids=["http-400", "http-200"])
def test_a_graphql_ratelimited_error_is_a_rate_limit(tmp_path, monkeypatch, urlopen):
    """Linear's RATELIMITED arrives as a GraphQL error (on an HTTP 400). It must be
    classified `rate_limited` — a FAILED read (None) that starts the back-off like a 429 —
    not `graphql`/`http`/`malformed`, which would hammer Linear every tick."""
    calls = []

    def counting(req, timeout=None):
        calls.append(1)
        return urlopen(req, timeout)

    with pytest.raises(LinearError) as e:
        linear._http_transport(SECRET, urlopen=urlopen)("query", {})
    assert e.value.kind == "rate_limited"

    clock = _Clock()
    monkeypatch.setattr(linear.time, "monotonic", clock)
    transport = linear._http_transport(SECRET, urlopen=counting)
    assert _src(tmp_path, transport).fetch_by_ids(["CMX-1"]) is None
    assert linear._backoff["CMX"][0] - clock.now == linear.BACKOFF_BASE_SECONDS
    second = _src(tmp_path, transport)
    assert second.list_open_tasks() == [] and second.read_failed is True
    assert len(calls) == 1                          # backing off: Linear was not called again


def test_reads_are_cached_within_one_tick(tmp_path):
    fake = FakeLinear([issue(1)])
    src = _src(tmp_path, fake)
    src.list_open_tasks()
    src.list_open_tasks()
    assert fake.names() == ["open"]


# --- the API key ---------------------------------------------------------------------

def test_the_key_is_read_from_chela_env_only(tmp_path, monkeypatch):
    env = tmp_path / "chela.env"
    env.write_text(f"LINEAR_API_KEY={SECRET}\n")
    monkeypatch.setenv("CHELA_ENV_FILE", str(env))
    seen = []
    monkeypatch.setattr(linear, "make_transport", lambda key: seen.append(key) or FakeLinear())
    assert LinearSource(_wf(tmp_path)).config_error is None
    assert seen == [SECRET]


def test_a_missing_key_is_a_startup_error_for_that_workflow(tmp_path, monkeypatch, caplog):
    env = tmp_path / "chela.env"
    env.write_text("CHELA_TMUX_SESSION=chela\n")
    monkeypatch.setenv("CHELA_ENV_FILE", str(env))
    monkeypatch.setenv("LINEAR_API_KEY", SECRET)      # an exported var is NOT the source
    src = LinearSource(_wf(tmp_path))
    assert "LINEAR_API_KEY" in src.config_error
    with caplog.at_level(logging.ERROR, logger="chela.sources.linear"):
        assert src.list_open_tasks() == [] and src.read_failed is True
    assert "no LINEAR_API_KEY in chela.env" in caplog.text


def test_a_missing_key_logs_one_error_not_one_per_tick(tmp_path, monkeypatch, caplog):
    env = tmp_path / "chela.env"
    env.write_text("CHELA_TMUX_SESSION=chela\n")
    monkeypatch.setenv("CHELA_ENV_FILE", str(env))
    with caplog.at_level(logging.ERROR, logger="chela.sources.linear"):
        for _ in range(3):                           # a fresh source every tick
            src = LinearSource(_wf(tmp_path))
            src.list_open_tasks()
            assert src.fetch_by_ids(["CMX-1"]) is None
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "no LINEAR_API_KEY in chela.env" in errors[0].getMessage()


def test_a_key_in_workflow_md_is_refused_not_used(tmp_path, monkeypatch):
    monkeypatch.setattr(linear, "make_transport", lambda key: pytest.fail("must not build"))
    src = LinearSource(_wf(tmp_path, api_key=SECRET))
    assert src.config_error and "tracker.api_key" in src.config_error
    assert SECRET not in src.config_error


def test_the_key_never_reaches_the_logs(tmp_path, monkeypatch, caplog):
    """Every path that logs — a failed read of each kind, a missing team, a refused
    WORKFLOW.md key, a successful close — and not one log line carries the key."""
    monkeypatch.setattr(linear, "make_transport", lambda key: linear._http_transport(
        key, urlopen=FAILURES["401"]))
    env = tmp_path / "chela.env"
    env.write_text(f"LINEAR_API_KEY={SECRET}\n")
    monkeypatch.setenv("CHELA_ENV_FILE", str(env))
    with caplog.at_level(logging.DEBUG):
        for failure in sorted(FAILURES):
            linear._backoff.clear()
            src = _src(tmp_path, linear._http_transport(SECRET, urlopen=FAILURES[failure]))
            src.list_open_tasks()
            src.fetch_by_ids(["CMX-1"])
            src.close_tasks(["CMX-1"])
            src.archive_sweep(force=True)
        LinearSource(_wf(tmp_path)).list_open_tasks()
        LinearSource(_wf(tmp_path, api_key=SECRET)).list_open_tasks()
        LinearSource(_wf(tmp_path, team="")).list_open_tasks()
    assert caplog.records, "the paths above must actually log"
    assert SECRET not in caplog.text


def test_the_key_never_reaches_a_child_env():
    """A dispatched agent, a judge, a hook or a suite gets `child_env()` — never the key."""
    env = envutil.child_env(base={"PATH": "/usr/bin", "LINEAR_API_KEY": SECRET})
    assert "LINEAR_API_KEY" not in env and env["PATH"] == "/usr/bin"


# --- run resolution across the shared CMX counter ------------------------------------

def _runs(*rows):
    return lambda: [dict(r) for r in rows]


OLD_TODO_ERA = {"task_id": "0123456789ab", "branch_name": "cmx-12", "window_name": "cmx-12",
                "status": "done", "pr_state": "merged"}
LINEAR_RUN = {"task_id": "CMX-12", "branch_name": "cmx-12-tighten-top-row",
              "window_name": "cmx-12-tighten-top-row", "status": "running", "pr_state": None}


def test_an_exact_linear_identifier_resolves_to_its_linear_run(monkeypatch):
    monkeypatch.setattr(dispatcher, "list_runs", _runs(OLD_TODO_ERA, LINEAR_RUN))
    assert dispatcher.resolve_run("CMX-12")["task_id"] == "CMX-12"


def test_an_ambiguous_cmx_n_never_resolves_to_an_old_done_todo_era_run(monkeypatch):
    monkeypatch.setattr(dispatcher, "list_runs", _runs(OLD_TODO_ERA, LINEAR_RUN))
    assert dispatcher.resolve_run("cmx-12")["task_id"] == "CMX-12"
    # Both terminal: still the Linear run, never the TODO.md-era one.
    done_linear = {**LINEAR_RUN, "status": "done", "pr_state": "merged"}
    monkeypatch.setattr(dispatcher, "list_runs", _runs(OLD_TODO_ERA, done_linear))
    assert dispatcher.resolve_run("cmx-12")["task_id"] == "CMX-12"
    # Both live: the Linear run.
    live_old = {**OLD_TODO_ERA, "status": "awaiting_review", "pr_state": "open"}
    monkeypatch.setattr(dispatcher, "list_runs", _runs(live_old, LINEAR_RUN))
    assert dispatcher.resolve_run("cmx-12")["task_id"] == "CMX-12"


def test_an_ambiguous_cmx_n_prefers_the_run_still_in_flight(monkeypatch):
    """The live TODO.md-era run outranks a finished Linear one — "in flight" comes first."""
    live_old = {**OLD_TODO_ERA, "status": "awaiting_review", "pr_state": "open"}
    done_linear = {**LINEAR_RUN, "status": "done", "pr_state": "merged"}
    monkeypatch.setattr(dispatcher, "list_runs", _runs(live_old, done_linear))
    assert dispatcher.resolve_run("cmx-12")["task_id"] == "0123456789ab"


TWIN_A = {**LINEAR_RUN, "workflow_path": "/a/WORKFLOW.md"}
TWIN_B = {**LINEAR_RUN, "workflow_path": "/b/WORKFLOW.md",
          "branch_name": "cmx-12-other", "window_name": "cmx-12"}


@pytest.mark.parametrize("twin_status", ["running", "done"])
@pytest.mark.parametrize("old", [None, "done", "awaiting_review"])
def test_two_tracker_runs_left_after_the_preference_resolve_to_none(
        monkeypatch, twin_status, old):
    """CMX-48: a wrong id is worse than no id. Two tracker-identifier runs both named by a
    bare `cmx-12`, neither preferred over the other ⇒ None, never whichever came first —
    whether they are both live or both finished, and whatever TODO.md-era run is around."""
    pr = "merged" if twin_status == "done" else None
    a = {**TWIN_A, "status": twin_status, "pr_state": pr}
    b = {**TWIN_B, "status": twin_status, "pr_state": pr}
    old_row = {**OLD_TODO_ERA, "status": old,
               "pr_state": "merged" if old == "done" else "open"}
    rows = [a, b] if old is None else [old_row, a, b]
    # The one unambiguous case: a lone LIVE TODO.md-era run beside two finished twins.
    want = "0123456789ab" if (old == "awaiting_review" and twin_status == "done") else None
    for order in (rows, list(reversed(rows))):
        monkeypatch.setattr(dispatcher, "list_runs", _runs(*order))
        got = dispatcher.resolve_run("cmx-12")
        assert (got and got["task_id"]) == want, order


def test_a_lone_todo_era_run_still_resolves(monkeypatch):
    monkeypatch.setattr(dispatcher, "list_runs", _runs(OLD_TODO_ERA))
    assert dispatcher.resolve_run("cmx-12")["task_id"] == "0123456789ab"


# --- the dispatcher, end to end -------------------------------------------------------

WORKFLOW = """---
project_key: CMX
tracker:
  kind: linear
  team: CMX
workspace:
  root: {root}
  base_branch: dev
concurrency:
  max: 2
---
brief: {{{{task_body}}}}
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "CHELA_DIR", state)
    monkeypatch.setattr(dispatcher, "CHELA_DIR", state)
    monkeypatch.setattr(dispatcher, "DB_PATH", state / "scheduler.db")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True,
                   capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"),
                 ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(work), "config", k, v], check=True,
                       capture_output=True)
    (work / "WORKFLOW.md").write_text(WORKFLOW.format(root=state / "worktrees"))
    (work / "README").write_text("x\n")
    for cmd in (["add", "-A"], ["commit", "-qm", "seed"], ["push", "-qu", "origin", "dev"]):
        subprocess.run(["git", "-C", str(work), *cmd], check=True, capture_output=True)
    return work


@pytest.fixture
def team(monkeypatch):
    fake = FakeLinear()
    monkeypatch.setattr(linear, "load_api_key", lambda: SECRET)
    monkeypatch.setattr(linear, "make_transport", lambda key: fake)
    return fake


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(dispatcher, "_launch_agent",
                        lambda wf, task_id, window, worktree, prompt, conn, **kw:
                        calls.append({"task_id": task_id, "window": window, "prompt": prompt}))
    monkeypatch.setattr(dispatcher, "_run_critic", lambda *a, **k: None)
    return calls


def _row(task_id):
    with dispatcher._db() as conn:
        r = conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()
        return dict(r) if r else None


def test_an_unblocked_todo_issue_is_dispatched_with_its_description_as_the_brief(
        repo, team, launched):
    """⭐ The case that must be ACCEPTED."""
    desc = "**Do.** tighten the top row\n\n**GUARDS.** corrupt it ⇒ RED"
    team.issues = {"CMX-7": issue(7, "Tighten top row", desc=desc,
                                  branch="cmx-7-tighten-top-row")}
    summary = dispatcher.tick(repo / "WORKFLOW.md")
    assert summary["dispatched"] == 1, summary
    row = _row("CMX-7")
    assert row["brief"] == desc
    assert (row["task_number"], row["branch_name"], row["window_name"]) == (
        7, "cmx-7-tighten-top-row", "cmx-7-tighten-top-row")
    assert launched == [{"task_id": "CMX-7", "window": "cmx-7-tighten-top-row",
                         "prompt": f"brief: {desc}"}]


def test_a_branch_name_the_remote_already_has_is_never_reused(repo, team, launched):
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "dev:cmx-7-tighten-top-row"],
                   check=True, capture_output=True)
    team.issues = {"CMX-7": issue(7, "Tighten top row", branch="cmx-7-tighten-top-row")}
    dispatcher.tick(repo / "WORKFLOW.md")
    assert _row("CMX-7")["branch_name"] == "cmx-7-tighten-top-row-2"


def test_a_retry_keeps_the_branch_its_first_attempt_took(repo, team, launched):
    """Attempt 1 took `cmx-7-old-title` (and pushed it). The issue was renamed since, so
    Linear now suggests `cmx-7-new-title`. Attempt 2 must reuse attempt 1's branch — not
    the new suggestion, and not `cmx-7-old-title-2` because the remote already has it."""
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "dev:cmx-7-old-title"],
                   check=True, capture_output=True)
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at, "
            "branch_name, window_name) VALUES ('CMX-7', ?, 't', 'failed', 1, ?, "
            "'cmx-7-old-title', 'cmx-7-old-title')",
            (str((repo / "WORKFLOW.md").resolve()), dispatcher._now()),
        )
        conn.commit()
    team.issues = {"CMX-7": issue(7, "New title", branch="cmx-7-new-title")}
    summary = dispatcher.tick(repo / "WORKFLOW.md")
    assert summary["dispatched"] == 1, summary
    row = _row("CMX-7")
    assert (row["attempt"], row["branch_name"]) == (2, "cmx-7-old-title")
    assert [c["window"] for c in launched] == ["cmx-7-old-title"]


def test_a_failed_read_changes_no_run(repo, team, launched):
    """A 401 on the tick's read ⇒ `read_failed` ⇒ a review-state run whose issue is
    absent from the (empty) read is NOT reconciled to done."""
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at, "
            "worktree_path) VALUES ('CMX-3', ?, 't', 'awaiting_review', 1, ?, '/nowhere')",
            (str((repo / "WORKFLOW.md").resolve()), dispatcher._now()),
        )
        conn.commit()
    team.fail = LinearError("auth", "HTTP 401")
    summary = dispatcher.tick(repo / "WORKFLOW.md")
    assert summary["tracker_read_failed"] is True
    assert _row("CMX-3")["status"] == "awaiting_review"


def test_a_closed_issue_reconciles_its_review_run_to_done(repo, team, launched):
    """The counterweight: a GOOD read without the issue (moved to Done) closes the run."""
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at, "
            "worktree_path) VALUES ('CMX-3', ?, 't', 'awaiting_review', 1, ?, NULL)",
            (str((repo / "WORKFLOW.md").resolve()), dispatcher._now()),
        )
        conn.commit()
    team.issues = {"CMX-3": issue(3, state="Done")}
    with patch.object(dispatcher, "_read_pr_url", return_value=None):
        dispatcher.tick(repo / "WORKFLOW.md")
    assert _row("CMX-3")["status"] == "done"


def test_get_source_selects_linear(tmp_path, team):
    from chela.sources import get_source

    assert isinstance(get_source(_wf(tmp_path)), LinearSource)


def test_the_tick_runs_the_archive_sweep(repo, team, launched):
    """An issue the GitHub integration closed is archived by the daemon's own tick."""
    team.issues = {"CMX-2": issue(2, state="Done")}
    summary = dispatcher.tick(repo / "WORKFLOW.md")
    assert summary["tracker_archived"] == 1
    assert team.issues["CMX-2"]["archivedAt"]


# --- CMX-432 rework 2: each guard asserts the invariant itself ------------------------

def test_the_production_transport_sends_the_key_to_linears_graphql_endpoint(tmp_path):
    """The real request — not a stub's view of it: POST to Linear's GraphQL URL, the key
    from chela.env as `Authorization`, a JSON body carrying the query and its variables."""
    seen = []

    def urlopen(req, timeout=None):
        seen.append((req, timeout))
        return _urlopen_returning(json.dumps({"data": {"ok": 1}}).encode())(req, timeout)

    assert linear._http_transport(SECRET, urlopen=urlopen)("query Q { x }", {"a": 1}) == {
        "ok": 1}
    (req, timeout), = seen
    assert req.full_url == linear.LINEAR_API_URL == "https://api.linear.app/graphql"
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == SECRET
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"query": "query Q { x }", "variables": {"a": 1}}
    assert timeout == linear.HTTP_TIMEOUT_SECONDS


def test_make_transport_is_the_http_transport_carrying_that_key(tmp_path, monkeypatch):
    """The seam the suite replaces must, unreplaced, authenticate with the key it is given."""
    seen = []

    def urlopen(req, timeout=None):
        seen.append(req.get_header("Authorization"))
        return _urlopen_returning(b'{"data": {}}')(req, timeout)

    monkeypatch.setattr(linear._http_transport, "__defaults__", (urlopen,))
    linear.make_transport(SECRET)("q", {})
    assert seen == [SECRET]


@pytest.mark.parametrize("code,kind", [(401, "auth"), (403, "auth"), (429, "rate_limited"),
                                       (500, "http"), (502, "http")])
def test_each_http_failure_is_classified_by_its_status(code, kind):
    with pytest.raises(LinearError) as e:
        linear._http_transport(SECRET, urlopen=_urlopen_raising(_http_error(code)))("q", {})
    assert e.value.kind == kind
    assert SECRET not in str(e.value)


@pytest.mark.parametrize("payload,kind", [
    (b"<html>gateway</html>", "malformed"),
    (b"[]", "malformed"),
    (b'{"data": null}', "malformed"),
    (json.dumps({"errors": [{"message": "who", "extensions": {
        "code": "AUTHENTICATION_ERROR"}}]}).encode(), "auth"),
    (json.dumps({"errors": [{"message": "no", "extensions": {
        "code": "FORBIDDEN"}}]}).encode(), "auth"),
    (json.dumps({"errors": [{"message": "bad field"}]}).encode(), "graphql"),
])
def test_each_response_failure_is_classified_by_its_body(payload, kind):
    with pytest.raises(LinearError) as e:
        linear._http_transport(SECRET, urlopen=_urlopen_returning(payload))("q", {})
    assert e.value.kind == kind


def test_a_network_failure_is_classified_network():
    with pytest.raises(LinearError) as e:
        linear._http_transport(SECRET, urlopen=_urlopen_raising(
            urllib.error.URLError("no route")))("q", {})
    assert e.value.kind == "network"


def test_a_transport_that_raises_anything_is_a_failed_read_not_a_crash(tmp_path):
    def boom(query, variables):
        raise RuntimeError("stub blew up")

    src = _src(tmp_path, boom)
    assert src.fetch_by_ids(["CMX-1"]) is None
    assert src.list_open_tasks() == [] and src.read_failed is True
    assert _src(tmp_path, lambda q, v: ["not", "a", "dict"]).fetch_by_ids(["CMX-1"]) is None


@pytest.mark.parametrize("key", ["api_key", "apiKey", "API_KEY", "apikey", "token",
                                 "auth_token", "secret", "client_secret", "password",
                                 "Password"])
def test_every_credential_shaped_workflow_key_is_refused(tmp_path, monkeypatch, key):
    """Not only `api_key`: any token/secret/password-shaped key under `tracker:` would put
    a credential in a repo file. Named in the error, never echoed, and nothing is built."""
    monkeypatch.setattr(linear, "load_api_key", lambda: SECRET)
    monkeypatch.setattr(linear, "make_transport", lambda k: pytest.fail("must not build"))
    src = LinearSource(_wf(tmp_path, **{key: SECRET}))
    assert src.config_error and f"tracker.{key}" in src.config_error
    assert SECRET not in src.config_error
    assert src.fetch_by_ids(["CMX-1"]) is None


def test_the_ordinary_tracker_keys_are_not_mistaken_for_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(linear, "load_api_key", lambda: SECRET)
    monkeypatch.setattr(linear, "make_transport", lambda k: FakeLinear())
    src = LinearSource(_wf(tmp_path, ready_states=["Todo"], done_state="Done"))
    assert src.config_error is None


def test_a_blank_key_line_is_a_missing_key(tmp_path, monkeypatch):
    env = tmp_path / "chela.env"
    env.write_text("LINEAR_API_KEY=   \n")
    monkeypatch.setenv("CHELA_ENV_FILE", str(env))
    assert "no LINEAR_API_KEY" in LinearSource(_wf(tmp_path)).config_error


@pytest.mark.parametrize("foreign", ["ENG-5", "CMXX-5", "XCMX-5", "5", "CMX-5x", "CMX-",
                                     "", None, "0123456789ab"])
def test_an_id_outside_this_team_is_never_this_trackers(tmp_path, foreign):
    """`ENG-5` is another team's issue 5, and a TODO.md-era hash is no issue at all — the
    team's own CMX-5 must not come back for it, and Linear is not even asked."""
    fake = FakeLinear([issue(5)])
    assert _src(tmp_path, fake).fetch_by_ids([foreign]) == []
    assert fake.calls == []


def test_only_this_teams_numbers_are_requested(tmp_path):
    fake = FakeLinear([issue(5), issue(6)])
    got = _src(tmp_path, fake).fetch_by_ids(["ENG-6", "cmx-5", "CMX-5", "OPS-7"])
    assert [t.id for t in got] == ["CMX-5"]
    assert fake.calls == [("by_number", {"team": "CMX", "numbers": [5], "after": None})]
    # A lowercase identifier (`chela peek cmx-6`) is still this team's issue 6.
    assert [t.id for t in _src(tmp_path, fake).fetch_by_ids(["cmx-6"])] == ["CMX-6"]


def test_a_canceled_issue_is_canceled_not_done(tmp_path):
    """Two terminal verdicts, kept apart: canceled work did not ship."""
    fake = FakeLinear([issue(1, state="Canceled"), issue(2, state="Done"),
                       issue(3, state="Canceled", archived=True),
                       issue(4, state="Done", archived=True), issue(5, state="Backlog")])
    got = {t.id: t.terminal_state
           for t in _src(tmp_path, fake).fetch_by_ids([f"CMX-{n}" for n in range(1, 6)])}
    assert got == {"CMX-1": "canceled", "CMX-2": "done", "CMX-3": "canceled",
                   "CMX-4": "done", "CMX-5": None}
    # G1's reconcile acts on `state`, not `terminal_state`: EVERY terminal verdict —
    # canceled as much as done — must read CLOSED there, or a canceled run never closes.
    states = {t.id: t.state
              for t in _src(tmp_path, fake).fetch_by_ids([f"CMX-{n}" for n in range(1, 6)])}
    assert states == {"CMX-1": "closed", "CMX-2": "closed", "CMX-3": "closed",
                      "CMX-4": "closed", "CMX-5": "open"}


@pytest.mark.parametrize("node_state", sorted(_TYPES))
def test_state_is_closed_exactly_when_terminal_state_is_set(tmp_path, node_state):
    """The invariant itself, over every state type: `state == "closed"` ⇔ the issue is
    terminal (any terminal verdict), for live and archived records alike."""
    for archived in (False, True):
        fake = FakeLinear([issue(1, state=node_state, archived=archived)])
        (t,) = _src(tmp_path, fake).fetch_by_ids(["CMX-1"])
        terminal = archived or _TYPES[node_state] in ("completed", "canceled")
        assert (t.terminal_state is not None) is terminal
        assert t.state == ("closed" if terminal else "open"), (node_state, archived)


# --- only THIS team ------------------------------------------------------------------

def _two_teams():
    return FakeLinear([issue(1), issue(2, state="In Progress"), issue(3, state="Done"),
                       issue(1, team="ENG"), issue(4, team="ENG", state="Backlog"),
                       issue(5, team="ENG", state="Done"), issue(9, team="OPS")])


def test_the_open_set_reads_only_this_teams_issues(tmp_path):
    """Another team's issues are never listed, claimed or reconciled against — the
    query's own team filter (applied by the fake from the query text) must keep them out."""
    fake = _two_teams()
    src = _src(tmp_path, fake)
    tasks = src.list_open_tasks()
    assert {t.id for t in tasks} == {"CMX-1", "CMX-2"}
    candidates, _ = src.claimable(tasks)
    assert [t.id for t in candidates] == ["CMX-1"]
    assert all(v["team"] == "CMX" for _, v in fake.calls)


def test_fetch_by_ids_reads_only_this_teams_issues(tmp_path):
    """ENG-1 shares CMX-1's number; the ID refresh must still return CMX-1 alone."""
    fake = _two_teams()
    got = _src(tmp_path, fake).fetch_by_ids(["CMX-1", "CMX-3"])
    assert sorted(t.id for t in got) == ["CMX-1", "CMX-3"]


def test_fetch_by_ids_returns_only_the_requested_numbers(tmp_path):
    fake = FakeLinear([issue(n) for n in range(1, 6)])
    got = _src(tmp_path, fake).fetch_by_ids(["CMX-2", "CMX-4"])
    assert sorted(t.id for t in got) == ["CMX-2", "CMX-4"]


def test_the_sweep_never_archives_another_teams_issue(tmp_path):
    fake = _two_teams()
    _src(tmp_path, fake).archive_sweep(force=True)
    archived = {v["id"] for n, v in fake.calls if n == "archive"}
    assert archived == {"uuid-3"}


# --- a response without its `issues` connection is a FAILED read ---------------------

MISSING_CONNECTION = {
    "no-issues-key": {},
    "issues-null": {"issues": None},
    "issues-not-object": {"issues": []},
    "nodes-missing": {"issues": {"pageInfo": {"hasNextPage": False}}},
    "nodes-not-list": {"issues": {"nodes": {"CMX-1": {}}}},
}


@pytest.mark.parametrize("shape", sorted(MISSING_CONNECTION))
def test_a_response_without_an_issues_connection_is_a_failed_read(tmp_path, shape):
    """`[]` here would read as "none of these issues exist any more" and close every
    live run — a response that is not the connection we asked for is a FAILED read."""
    payload = MISSING_CONNECTION[shape]
    src = _src(tmp_path, lambda q, v: payload)
    assert src.fetch_by_ids(["CMX-1"]) is None
    src = _src(tmp_path, lambda q, v: payload)
    assert src.list_open_tasks() == [] and src.read_failed is True


@pytest.mark.parametrize("shape", sorted(MISSING_CONNECTION))
def test_a_missing_connection_on_a_later_page_is_a_failed_read(tmp_path, shape):
    """Page 1 is good, page 2 is not: the partial list must not pass as the whole."""
    first = {"issues": {"nodes": [issue(1)],
                        "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}

    def transport(q, v):
        return first if v.get("after") is None else MISSING_CONNECTION[shape]

    assert _src(tmp_path, transport).fetch_by_ids(["CMX-1", "CMX-2"]) is None
    src = _src(tmp_path, transport)
    assert src.list_open_tasks() == [] and src.read_failed is True


@pytest.mark.parametrize("status", ["awaiting_review", "running"])
def test_a_missing_connection_changes_no_run(repo, team, launched, monkeypatch, status):
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at, "
            "worktree_path) VALUES ('CMX-3', ?, 't', ?, 1, ?, '/nowhere')",
            (str((repo / "WORKFLOW.md").resolve()), status, dispatcher._now()),
        )
        conn.commit()
    monkeypatch.setattr(linear, "make_transport", lambda key: lambda q, v: {"issues": None})
    with patch.object(dispatcher, "_read_pr_url", return_value=None):
        dispatcher.tick(repo / "WORKFLOW.md")
    assert _row("CMX-3")["status"] == status


def test_a_priority_and_order_tie_is_broken_by_the_issue_number(tmp_path):
    """Same priority, same sortOrder, listed out of order by Linear: the number decides,
    so the claim order never depends on how the API happened to page."""
    fake = FakeLinear([issue(3, priority=2, sort=1.0), issue(1, priority=2, sort=1.0),
                       issue(2, priority=2, sort=1.0), issue(9, priority=1, sort=9.0)])
    assert [t.id for t in _src(tmp_path, fake).list_open_tasks()] == [
        "CMX-9", "CMX-1", "CMX-2", "CMX-3"]
    assert _claim(tmp_path, fake) == ["CMX-9", "CMX-1", "CMX-2", "CMX-3"]


@pytest.mark.parametrize("rel_type", ["related", "duplicate", "similar", None])
def test_only_a_blocks_relation_holds_a_task(tmp_path, rel_type):
    """`inverseRelations` carries every relation type pointing at the issue; a related or
    duplicate link to an OPEN issue is not a blocker and must not hold the task."""
    rel = {"type": rel_type, "issue": {"identifier": "CMX-2", "archivedAt": None,
                                       "state": {"type": "started"}}}
    fake = FakeLinear([issue(1, blockers=[rel]), issue(2, state="In Progress")])
    t = {t.id: t for t in _src(tmp_path, fake).list_open_tasks()}["CMX-1"]
    assert t.depends == ()
    assert _claim(tmp_path, fake) == ["CMX-1"]
    # … while the same link typed `blocks` does hold it.
    fake = FakeLinear([issue(1, blockers=[{**rel, "type": "blocks"}]),
                       issue(2, state="In Progress")])
    assert _claim(tmp_path, fake) == []


def test_closing_an_issue_archived_while_still_open_writes_nothing(tmp_path):
    """An issue archived straight out of Todo is already terminal for chela: no state
    update (Linear would un-archive or reject it) and no second archive."""
    fake = FakeLinear([issue(4, state="Todo", archived=True)])
    assert _src(tmp_path, fake).close_tasks(["CMX-4"]) == {"CMX-4": "already"}
    assert fake.names() == ["by_number"]
    assert fake.issues["CMX-4"]["state"]["name"] == "Todo"


def test_a_close_whose_read_fails_is_failed_and_an_unknown_issue_is_missing(tmp_path):
    fake = FakeLinear([issue(4, state="In Review")], fail=LinearError("network", "x"))
    assert _src(tmp_path, fake).close_tasks(["CMX-4"]) == {"CMX-4": "failed"}
    fake.fail = None
    assert _src(tmp_path, fake).close_tasks(["CMX-8"]) == {"CMX-8": "missing"}
    assert "update" not in fake.names() and "archive" not in fake.names()


def test_a_close_that_raises_never_crashes_the_strike(tmp_path):
    src = _src(tmp_path, FakeLinear())
    src.close_tasks = lambda ids, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    assert dispatcher._strike_merged_tasks(_wf(tmp_path), src, ["CMX-4"]) == 0


def test_list_open_tasks_follows_every_page(tmp_path):
    """Linear pages at 100; the second page's issues are open work too, fetched with the
    first page's `endCursor`."""
    calls = []

    def paged(query, variables):
        calls.append(variables.get("after"))
        if variables.get("after") is None:
            return {"issues": {"nodes": [issue(1)],
                               "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}
        assert variables["after"] == "c1"
        return {"issues": {"nodes": [issue(2)],
                           "pageInfo": {"hasNextPage": False, "endCursor": "c2"}}}

    assert [t.id for t in _src(tmp_path, paged).list_open_tasks()] == ["CMX-1", "CMX-2"]
    assert calls == [None, "c1"]


def test_a_risk_label_sets_the_task_risk(tmp_path):
    node = issue(1)
    node["labels"] = {"nodes": [{"name": "risk:high"}, {"name": "ui"}]}
    t = _src(tmp_path, FakeLinear([node])).list_open_tasks()[0]
    assert (t.risk, t.risk_reason) == ("high", "label")


def test_a_held_task_logs_info_for_an_open_blocker_and_warns_for_an_unknown_one(
        tmp_path, caplog):
    """An In Progress blocker is open (just not claimable): an ordinary wait, INFO. A
    blocker the team does not have open or done is a tracker problem: WARNING."""
    fake = FakeLinear([issue(1, blockers=[blocked_by("CMX-2", "In Progress")]),
                       issue(2, state="In Progress"),
                       issue(3, blockers=[blocked_by("CMX-77", "Canceled")])])
    with caplog.at_level(logging.INFO, logger="chela.dispatcher"):
        assert _claim(tmp_path, fake) == []
    by_task = {("CMX-1" in r.getMessage(), "CMX-3" in r.getMessage()): r.levelno
               for r in caplog.records if "held back" in r.getMessage()}
    assert by_task == {(True, False): logging.INFO, (False, True): logging.WARNING}


def test_the_sweep_warns_above_the_threshold(tmp_path, caplog):
    fake = FakeLinear([issue(i) for i in range(1, linear.ISSUE_COUNT_WARN_AT + 2)])
    with caplog.at_level(logging.WARNING, logger="chela.sources.linear"):
        assert _src(tmp_path, fake).archive_sweep() == 0
    assert f"{linear.ISSUE_COUNT_WARN_AT + 1} non-archived issues" in caplog.text
    linear._last_sweep.clear()
    caplog.clear()
    fake = FakeLinear([issue(i) for i in range(1, linear.ISSUE_COUNT_WARN_AT + 1)])
    with caplog.at_level(logging.WARNING, logger="chela.sources.linear"):
        _src(tmp_path, fake).archive_sweep()
    assert "non-archived issues" not in caplog.text


def test_dry_run_previews_the_trackers_branch_and_number(repo, team):
    """The preview must name the branch a live tick would take — Linear's, not
    `{project_key}-{N}` — and Linear's own number."""
    desc = "**Do.** tighten the top row"
    team.issues = {"CMX-7": issue(7, "Tighten top row", desc=desc,
                                  branch="cmx-7-tighten-top-row")}
    plan, = dispatcher.dry_run(repo / "WORKFLOW.md")
    assert (plan["task_id"], plan["task_number"], plan["branch"]) == (
        "CMX-7", 7, "cmx-7-tighten-top-row")
    assert plan["prompt"] == f"brief: {desc}"


def test_the_worktree_is_checked_out_on_the_trackers_branch(repo, team, launched):
    team.issues = {"CMX-7": issue(7, "Tighten top row", branch="cmx-7-tighten-top-row")}
    dispatcher.tick(repo / "WORKFLOW.md")
    row = _row("CMX-7")
    head = subprocess.run(["git", "-C", row["worktree_path"], "rev-parse", "--abbrev-ref",
                           "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    assert head == row["branch_name"] == "cmx-7-tighten-top-row"


def test_every_taken_suffix_is_skipped(repo, team, launched):
    for b in ("cmx-7-tighten-top-row", "cmx-7-tighten-top-row-2"):
        subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", f"dev:{b}"],
                       check=True, capture_output=True)
    team.issues = {"CMX-7": issue(7, "Tighten top row", branch="cmx-7-tighten-top-row")}
    dispatcher.tick(repo / "WORKFLOW.md")
    assert _row("CMX-7")["branch_name"] == "cmx-7-tighten-top-row-3"


def test_a_failed_read_skips_the_archive_sweep(repo, team, launched):
    team.fail = LinearError("auth", "HTTP 401")
    summary = dispatcher.tick(repo / "WORKFLOW.md")
    assert summary["tracker_read_failed"] is True
    assert "sweep" not in team.names() and "tracker_archived" not in summary


# --- CMX-432 rework 3: the guards the judge's mutations walked past -------------------

def test_an_archive_refused_with_success_false_is_a_failed_archive(tmp_path, caplog):
    """Linear's OTHER refusal shape — `issueArchive { success: false }` on a 200, no error
    raised. It is a failed archive: `archive_issue` says False, the sweep does not count it
    as archived, and the published count still includes the issue (it is still there)."""
    fake = FakeLinear([issue(1, state="Done"), issue(2, state="Canceled"), issue(3)])
    fake.archive_success = False
    src = _src(tmp_path, fake)
    with caplog.at_level(logging.WARNING, logger="chela.sources.linear"):
        assert src.archive_issue("uuid-1", "CMX-1") is False
        assert src.archive_sweep(force=True) == 0
    assert "archiving CMX-1 was refused" in caplog.text
    assert linear.read_published_counts()["CMX"]["count"] == 3
    assert fake.names().count("archive") == 3        # tried, and each one refused
    # The same reply after a close: still unarchived, so the sweep retries it later.
    fake.calls.clear()
    assert _src(tmp_path, fake).close_tasks(["CMX-1"]) == {"CMX-1": "already"}
    assert fake.names().count("archive") == 1
    assert fake.issues["CMX-1"]["archivedAt"] is None


def test_an_accepted_archive_is_a_success(tmp_path):
    """The counterweight: `success: true` IS an archive."""
    fake = FakeLinear([issue(1, state="Done")])
    assert _src(tmp_path, fake).archive_issue("uuid-1", "CMX-1") is True
    assert fake.issues["CMX-1"]["archivedAt"]


def _malformed(n, shape):
    bad = issue(n)
    if shape == "not-a-dict":
        return ["not", "an", "issue"]
    if shape == "no-identifier":
        bad["identifier"] = None
    elif shape == "title-not-text":
        bad["title"] = 42
    elif shape == "number-not-int":
        bad["number"] = "twelve"
    elif shape == "state-not-dict":
        bad["state"] = "Todo"
    return bad


MALFORMED = ["not-a-dict", "no-identifier", "title-not-text", "number-not-int",
             "state-not-dict"]


@pytest.mark.parametrize("shape", MALFORMED)
def test_a_malformed_open_record_drops_only_that_record(tmp_path, caplog, shape):
    """SPEC 11.1: the open-set read MAY drop a malformed record — and ONLY that record. An
    empty open set on a GOOD read means "nothing is open", which reconciles every live
    run to done; one bad record must never cost the others."""
    fake = FakeLinear([issue(1), issue(3, state="In Progress")])
    fake.issues["bad"] = _malformed(2, shape)
    src = _src(tmp_path, fake)
    with caplog.at_level(logging.WARNING, logger="chela.sources.linear"):
        tasks = src.list_open_tasks()
    assert [t.id for t in tasks] == ["CMX-1", "CMX-3"]
    assert src.read_failed is False
    assert "skipping a malformed issue record" in caplog.text


@pytest.mark.parametrize("shape", MALFORMED)
def test_each_malformed_requested_record_fails_the_whole_refresh(tmp_path, shape):
    """…while the ID refresh MUST fail rather than omit a requested record."""
    fake = FakeLinear([issue(1)])
    bad = _malformed(2, shape)
    fake.issues["bad"] = bad
    assert _src(tmp_path, fake).fetch_by_ids(["CMX-1", "CMX-2"]) is None


def test_a_malformed_open_record_never_reconciles_a_live_run(repo, team, launched):
    """End to end: the run whose issue IS readable stays where it is."""
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, attempt, started_at, "
            "worktree_path) VALUES ('CMX-3', ?, 't', 'awaiting_review', 1, ?, '/nowhere')",
            (str((repo / "WORKFLOW.md").resolve()), dispatcher._now()),
        )
        conn.commit()
    team.issues = {"CMX-3": issue(3, state="In Review"), "bad": _malformed(2, "state-not-dict")}
    summary = dispatcher.tick(repo / "WORKFLOW.md")
    assert summary.get("tracker_read_failed") is not True
    assert _row("CMX-3")["status"] == "awaiting_review"


def test_publishing_one_teams_count_keeps_every_other_teams(tmp_path):
    """The counts file holds every linear team's entry; publishing one replaces only that
    team's — doctor reads each declared team from the same file."""
    linear._publish_count("CMX", 12)
    linear._publish_count("OPS", 230)
    linear._publish_count("CMX", 13)
    counts = linear.read_published_counts()
    assert {t: e["count"] for t, e in counts.items()} == {"CMX": 13, "OPS": 230}
    found = runtime_truth._linear_issue_cap_report(
        {"CMX": "a.md", "OPS": "b.md"}, runtime_truth._linear_issue_cap_read())
    assert [(f.level, "OPS" in f.title) for f in found] == [
        (runtime_truth.OK, False), (runtime_truth.WARN, True)]


def test_two_teams_sweeps_each_publish_their_own_count(tmp_path):
    a = FakeLinear([issue(1, state="Done"), issue(2), issue(3)])
    b = FakeLinear([issue(n, team="OPS") for n in (1, 2, 3, 4)])
    linear.LinearSource(_wf(tmp_path), transport=a).archive_sweep(force=True)
    wf_b = _wf(tmp_path)
    wf_b.config["tracker"]["team"] = "OPS"
    linear.LinearSource(wf_b, transport=b).archive_sweep(force=True)
    counts = linear.read_published_counts()
    assert (counts["CMX"]["count"], counts["OPS"]["count"]) == (2, 4)


def test_a_team_with_no_published_count_is_ok_not_a_warning(tmp_path):
    linear._publish_count("OPS", 999)
    found = runtime_truth._linear_issue_cap_report(
        {"CMX": "WORKFLOW.md"}, runtime_truth._linear_issue_cap_read())
    assert [f.level for f in found] == [runtime_truth.OK]
    assert "no issue count published yet" in found[0].title


def _push_from_elsewhere(repo, tmp_path, branch):
    """Put ``branch`` on origin WITHOUT this clone hearing of it — no refs/remotes/origin
    entry here. Only asking the remote can see it."""
    other = tmp_path / "other"
    origin = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                            capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "clone", "-q", origin, str(other)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin", f"HEAD:{branch}"],
                   check=True, capture_output=True)
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
                           f"refs/remotes/origin/{branch}"]).returncode != 0


def test_a_branch_only_the_remote_has_is_never_reused(repo, team, launched, tmp_path):
    """The name was taken on origin by someone else (another clone, a human). This clone's
    refs/remotes/origin has never heard of it — only `ls-remote` can tell."""
    _push_from_elsewhere(repo, tmp_path, "cmx-7-tighten-top-row")
    team.issues = {"CMX-7": issue(7, "Tighten top row", branch="cmx-7-tighten-top-row")}
    dispatcher.tick(repo / "WORKFLOW.md")
    assert _row("CMX-7")["branch_name"] == "cmx-7-tighten-top-row-2"


def test_a_stale_tracking_ref_the_remote_deleted_does_not_take_the_name(repo, team, launched):
    """The other direction: this clone remembers `origin/<branch>`, but origin deleted it
    (a merged PR's branch). The remote is the authority, so the name is free."""
    subprocess.run(["git", "-C", str(repo), "update-ref",
                    "refs/remotes/origin/cmx-7-tighten-top-row", "HEAD"], check=True)
    team.issues = {"CMX-7": issue(7, "Tighten top row", branch="cmx-7-tighten-top-row")}
    dispatcher.tick(repo / "WORKFLOW.md")
    assert _row("CMX-7")["branch_name"] == "cmx-7-tighten-top-row"


def test_the_remote_branch_check_falls_back_to_tracking_refs_when_origin_is_unreachable(
        tmp_path):
    """No answer from origin (exit 128, not 0/2) — the clone's own refs are the best it
    knows."""
    work = tmp_path / "w"
    subprocess.run(["git", "init", "-q", "-b", "dev", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(tmp_path / "gone")],
                   check=True)
    subprocess.run(["git", "-C", str(work), "-c", "user.email=t@e", "-c", "user.name=T",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    subprocess.run(["git", "-C", str(work), "update-ref", "refs/remotes/origin/taken", "HEAD"],
                   check=True)
    assert dispatcher._remote_branch_exists(work, "taken") is True
    assert dispatcher._remote_branch_exists(work, "free") is False


def test_doctor_names_a_linear_workflows_tracker_kind(tmp_path, monkeypatch):
    """`chela doctor`'s dispatch.workflows line, read end to end: a linear workflow has no
    tracker FILE, so the line must name its kind — not gh_issues, not a blank."""
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\nproject_key: CMX\ntracker:\n  kind: linear\n  team: CMX\n---\nx\n")
    monkeypatch.setattr(runtime_truth, "dispatched_workflows", lambda: [wf])
    monkeypatch.setattr(linear, "load_api_key", lambda: SECRET)
    found = runtime_truth._workflows_report([wf], runtime_truth._workflows_read())
    assert [(f.level, f.detail) for f in found] == [(runtime_truth.OK, "tracker: linear")]
