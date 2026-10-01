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
          archived=False, branch=None):
    return {
        "id": f"uuid-{n}", "identifier": f"CMX-{n}", "number": n,
        "title": title or f"task {n}", "description": desc, "priority": priority,
        "sortOrder": sort, "url": f"https://linear.app/x/issue/CMX-{n}",
        "branchName": branch if branch is not None else f"cmx-{n}-task-{n}",
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
        nodes = [i for i in self.issues.values() if _matches(query, i)]
        if name == "by_number":
            return self._page([i for i in nodes if i["number"] in variables["numbers"]])
        if name in ("open", "sweep"):
            return self._page(nodes)
        if name == "states":
            return {"teams": {"nodes": [{"id": "team", "states": {"nodes": [
                {"id": f"st-{k}", "name": k, "type": t, "position": p}
                for p, (k, t) in enumerate(_TYPES.items())]}}]}}
        target = next(i for i in nodes if i["id"] == variables["id"])
        if name == "update":
            sid = variables["stateId"][len("st-"):]
            target["state"] = {"name": sid, "type": _TYPES[sid]}
            return {"issueUpdate": {"success": True}}
        if self.refuse_archive:
            raise LinearError("graphql", "archive refused")
        target["archivedAt"] = "2026-10-01T12:00:00Z"
        return {"issueArchive": {"success": True}}

    def names(self):
        return [n for n, _ in self.calls]


def _matches(query: str, node: dict) -> bool:
    """Apply the filter the QUERY TEXT asks for, the way Linear would — so a query whose
    filter is wrong returns the wrong issues here too, instead of the fake knowing better."""
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
