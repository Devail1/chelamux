"""CMX-434: the sidebar's "Suggest names" — proposals from real content, and a SAFE title apply.

The dangerous half is :func:`chela.suggest_names.deliver_title`: it types ``/rename <title>``
into a live pane. It must type ONLY into a pane where a Claude process runs, idle, with no
dialog showing and an empty prompt. A busy or blocked session is queued and applied on idle
by :func:`chela.suggest_names.flush_pending`.

No live tmux: a fake tmux keeps per-window user options; the claude pid, the native status,
the pane capture and the send are stubbed at their seams.
"""
from __future__ import annotations

import json
import subprocess
import types
from pathlib import Path

import pytest

from chela import agent_manager, discovery, messenger, sessions, suggest_names, transcripts
from chela.dashboard import app as dash

# Real `capture-pane -e` shapes.
EMPTY = "\x1b[39m❯ \x1b[0m\n"
GHOST = '\x1b[39m❯ \x1b[2mTry "fix lint errors"\x1b[0m\n'
DRAFT = "\x1b[39m❯ half-typed thought\n"
BASH = "user@host:~/proj$ \n"
# Claude Code's folder-trust prompt (current wording) and a permission prompt.
TRUST = (" Accessing workspace:\n\n /home/u/proj\n\n Quick safety check: Is this a project you"
         " created or one you trust?\n\n \x1b[36m❯ 1. Yes, I trust this folder\x1b[0m\n"
         "   2. No, exit\n\n Enter to confirm · Esc to cancel\n")
OLD_TRUST = " Do you trust the files in this folder?\n\n ❯ 1. Yes, proceed\n   2. No, exit\n"
PERMISSION = (" Bash command\n   rm -rf build\n Do you want to proceed?\n"
              " ❯ 1. Yes\n   2. No, and tell Claude what to do differently (esc)\n")
# A dialog drawn ABOVE an input line that reads empty: the prompt check alone passes it, so
# only the dialog check keeps the keys out (the negative control for that check).
DIALOG_OVER_EMPTY = "Do you trust the files in this folder?\n" + EMPTY


class _FakeTmux:
    def __init__(self, options=None, fail_list=False, fail_set=False):
        self.options: dict[str, dict[str, str]] = options or {}
        self.fail_list = fail_list
        self.fail_set = fail_set
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    @staticmethod
    def _wid(target):
        return target.rsplit(":", 1)[-1]

    def __call__(self, cmd, *a, **kw):
        self.calls.append(list(cmd))
        self.kwargs.append(kw)
        ok = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if self.fail_set and cmd[:2] == ["tmux", "set-window-option"]:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="bad option")
        if cmd[:2] == ["tmux", "display-message"]:
            wid = self._wid(cmd[cmd.index("-t") + 1])
            ok.stdout = self.options.get(wid, {}).get(cmd[-1][2:-1], "") + "\n"
        elif cmd[:2] == ["tmux", "set-window-option"]:
            if "-u" in cmd:
                self.options.setdefault(self._wid(cmd[cmd.index("-t") + 1]), {}).pop(cmd[-1], None)
            else:
                self.options.setdefault(self._wid(cmd[3]), {})[cmd[4]] = cmd[5]
        elif cmd[:2] == ["tmux", "list-windows"]:
            if self.fail_list:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="no server")
            ok.stdout = "".join(f"{w}\t{o.get(suggest_names.PENDING_OPTION, '')}\n"
                                for w, o in self.options.items())
        return ok

    def pending(self, wid):
        return self.options.get(wid, {}).get(suggest_names.PENDING_OPTION)


class _Fleet:
    """claude pids, native statuses, pane captures and the send, per window."""

    def __init__(self, monkeypatch, *, pids, status, panes, send_ok=True):
        self.pids, self.status, self.panes, self.send_ok = pids, status, panes, send_ok
        self.sent: list[tuple[str, str, dict]] = []
        self.tmux = _FakeTmux()
        monkeypatch.setattr(subprocess, "run", self.tmux)
        monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: self.pids.get(wid))
        monkeypatch.setattr(agent_manager, "session_status_map", lambda *a, **k: {
            "by_pid": {self.pids[w]: s for w, s in self.status.items() if w in self.pids}})
        monkeypatch.setattr(messenger, "capture_pane", self._capture)
        monkeypatch.setattr(messenger, "send_tmux", self._send)
        self.captures: list[tuple[str, bool]] = []

    def _capture(self, wid, ansi=False):
        self.captures.append((wid, ansi))
        return self.panes.get(wid, "")

    def _send(self, wid, text, **kw):
        self.sent.append((wid, text, kw))
        return self.send_ok


# ---------------------------------------------------------------------------
# Shaping a proposal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ("Tipstack API", "tipstack-api"),
    ("  --Fix__the  LOGIN bug!!--  ", "fix-the-login-bug"),
    ("a" * 30, "a" * 20),
    ("abcdefghijklmnopqrs-tuv", "abcdefghijklmnopqrs"),   # cut at 20 leaves no edge dash
    ("ünïcödé", "n-c-d"),
    ("!!!", ""),
    (None, ""),
])
def test_clean_name(raw, want):
    assert suggest_names.clean_name(raw) == want


@pytest.mark.parametrize("raw,want", [
    ("Tipstack API rate limits", "Tipstack API rate limits"),
    ("two\nlines\tand\x1b[31m escapes", "two lines and [31m escapes"),
    ("x" * 50, "x" * 40),
    ("   ", ""),
    (None, ""),
])
def test_clean_title(raw, want):
    assert suggest_names.clean_title(raw) == want


def test_unique_names_against_other_windows_and_each_other():
    windows = {"@1": "orchestrator", "@2": "a", "@3": "b", "@4": "tipstack"}
    got = suggest_names.unique_names({"@2": "tipstack", "@3": "tipstack", "@10": "orchestrator"},
                                     {**windows, "@10": "c"})
    # @4 keeps "tipstack" (not being renamed), @1 keeps "orchestrator".
    assert got == {"@2": "tipstack-2", "@3": "tipstack-3", "@10": "orchestrator-2"}


def test_unique_names_a_row_may_keep_its_own_name_and_suffixes_fit_20():
    windows = {"@1": "keep-me", "@2": "x" * 20}
    got = suggest_names.unique_names({"@1": "keep-me", "@3": "x" * 20}, {**windows, "@3": "y"})
    assert got == {"@1": "keep-me", "@3": "x" * 18 + "-2"}


def test_unique_names_never_proposes_another_windows_current_name():
    """Renames apply one at a time and the API refuses a held name, so no swap is proposed."""
    windows = {"@2": "a", "@3": "b"}
    assert suggest_names.unique_names({"@2": "b", "@3": "a"}, windows) == {"@2": "b-2", "@3": "a-2"}


# ---------------------------------------------------------------------------
# What a session is doing now
# ---------------------------------------------------------------------------

def _transcript(tmp_path: Path, records: list[dict], name: str = "s") -> Path:
    p = tmp_path / f"{name}.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in records))
    return p


def _user(text):
    return {"type": "user", "message": {"content": text}, "timestamp": "2026-11-18T10:00:00Z"}


def _assistant(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]},
            "timestamp": "2026-11-18T10:00:01Z"}


def test_content_prefers_the_latest_recap(tmp_path):
    p = _transcript(tmp_path, [_user("Tetris-style blocks"), _assistant("ok"),
                               {"type": "system", "subtype": "away_summary", "content": "old recap"},
                               {"type": "system", "subtype": "away_summary", "content": "Tipstack billing"}])
    assert suggest_names.content_for(p) == "Tipstack billing"


def test_content_without_a_recap_is_the_last_turns_never_the_first_prompt(tmp_path):
    turns = [_user("Tetris-style blocks")]
    for i in range(8):
        turns += [_user(f"now tipstack step {i}"), _assistant(f"did tipstack {i}")]
    got = suggest_names.content_for(_transcript(tmp_path, turns))
    assert "Tetris" not in got
    assert got.splitlines() == ["user: now tipstack step 5", "assistant: did tipstack 5",
                                "user: now tipstack step 6", "assistant: did tipstack 6",
                                "user: now tipstack step 7", "assistant: did tipstack 7"]


def test_content_none_for_no_path_or_nothing_said(tmp_path):
    assert suggest_names.content_for(None) is None
    assert suggest_names.content_for(_transcript(tmp_path, [{"type": "system"}])) is None


def test_current_title_custom_outranks_ai(tmp_path):
    p = _transcript(tmp_path, [{"type": "ai-title", "aiTitle": "Tetris-style blocks"}])
    assert suggest_names.current_title(p) == "Tetris-style blocks"
    with p.open("a") as fh:
        fh.write(json.dumps({"type": "custom-title", "customTitle": "Tipstack", "sessionId": "s"}) + "\n")
        fh.write(json.dumps({"type": "ai-title", "aiTitle": "Later ai title"}) + "\n")
    assert suggest_names.current_title(p) == "Tipstack"
    assert suggest_names.current_title(None) is None
    assert transcripts.summary_for_path(p)["custom_title"] == "Tipstack"
    assert transcripts.summary_for_path(None)["custom_title"] is None


def test_latest_custom_title_skips_blank(tmp_path):
    p = _transcript(tmp_path, [{"type": "custom-title", "customTitle": "  "}])
    assert transcripts.latest_custom_title(p) is None


# ---------------------------------------------------------------------------
# The proposals
# ---------------------------------------------------------------------------

class _Runner:
    def __init__(self, structured=None, error=""):
        self.structured, self.error = structured, error
        self.calls: list[dict] = []

    def run(self, prompt, *, cwd, schema, tools):
        self.calls.append({"prompt": prompt, "cwd": cwd, "schema": schema, "tools": tools,
                           "cwd_existed": Path(cwd).is_dir()})
        return types.SimpleNamespace(structured=self.structured, error=self.error)


def test_ask_model_one_tool_less_call_and_drops_invented_wids():
    r = _Runner({"suggestions": [{"wid": "@1", "name": "n", "title": "t"},
                                 {"wid": "@99", "name": "x", "title": "y"}, "junk"]})
    got, err = suggest_names.ask_model([{"wid": "@1", "doing": "billing"}], r)
    assert (got, err) == ({"@1": {"name": "n", "title": "t"}}, None)
    [call] = r.calls
    assert call["tools"] == ()
    assert call["schema"] is suggest_names.SCHEMA
    assert call["cwd_existed"] and not Path(call["cwd"]).exists()   # a scratch dir, cleaned up
    assert call["prompt"].endswith(json.dumps([{"wid": "@1", "doing": "billing"}], indent=1))


def test_ask_model_default_runner_is_haiku_headless(monkeypatch):
    from chela.judge_eval import runner as runner_mod
    made = {}

    class _CLI(_Runner):
        def __init__(self, model, **kw):
            made.update(model=model, **kw)
            super().__init__({"suggestions": []})

    monkeypatch.setattr(runner_mod, "ClaudeCLIRunner", _CLI)
    assert suggest_names.ask_model([{"wid": "@1", "doing": "x"}]) == ({}, None)
    assert made == {"model": "haiku", "max_budget_usd": 0.5, "timeout": 180}


@pytest.mark.parametrize("structured,error,want", [
    (None, "model call failed (rate)", "model call failed (rate)"),
    ({"suggestions": "nope"}, "", "the model returned no suggestions list"),
    ({"suggestions": [{"wid": "@1", "name": "n", "title": "t"}]}, "boom", "boom"),
])
def test_ask_model_errors(structured, error, want):
    assert suggest_names.ask_model([{"wid": "@1", "doing": "x"}], _Runner(structured, error)) == ({}, want)


@pytest.fixture
def suggest_env(monkeypatch, tmp_path):
    """Windows @1 (claude, recap), @2 (claude, nothing said), @3 (bash), @4 (claude, turns)."""
    paths = {
        "@1": _transcript(tmp_path, name="1", records=[{"type": "system", "subtype": "away_summary", "content": "Tipstack billing"},
                           {"type": "ai-title", "aiTitle": "Tetris-style blocks"}]),
        "@2": None,
        "@4": _transcript(tmp_path, name="4", records=[_user("start"), _assistant("lint fixes")]),
    }
    monkeypatch.setattr(discovery, "get_windows_by_id",
                        lambda: {"@1": "tetris", "@2": "Fresh_One", "@3": "bash", "@4": "w4", "@5": "taken"})
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: {"@1": 11, "@2": 12, "@4": 14}.get(wid))
    monkeypatch.setattr(agent_manager, "is_manual_name", lambda wid: wid == "@4")
    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid: paths.get(wid))
    tmux = _FakeTmux({"@4": {suggest_names.PENDING_OPTION: "Queued title"}})
    monkeypatch.setattr(subprocess, "run", tmux)
    return paths


def test_suggest_rows_from_real_content(suggest_env):
    r = _Runner({"suggestions": [{"wid": "@1", "name": "Tipstack Billing!", "title": "Tipstack billing " * 5},
                                 {"wid": "@4", "name": "taken", "title": "Lint fixes"}]})
    got = suggest_names.suggest(["@1", "@2", "@3", "@4", "@404"], r)
    assert got["error"] is None
    rows = {row["wid"]: row for row in got["rows"]}
    assert set(rows) == {"@1", "@2", "@4"}          # the bash pane and a dead wid get no row
    assert rows["@1"] == {"wid": "@1", "name": "tetris", "manual_name": False,
                          "title": "Tetris-style blocks", "title_pending": None, "content": True,
                          "note": None, "suggested_name": "tipstack-billing",
                          "suggested_title": ("Tipstack billing " * 5)[:40].rstrip()}
    # Nothing to read: no model entry, current values, and a note saying why.
    assert rows["@2"]["content"] is False
    assert rows["@2"]["suggested_name"] == "fresh-one"
    assert rows["@2"]["suggested_title"] == ""
    assert rows["@2"]["note"] == "nothing to read in this session yet"
    # "taken" belongs to @5, which is not being renamed.
    assert rows["@4"]["suggested_name"] == "taken-2"
    assert rows["@4"]["manual_name"] is True
    assert rows["@4"]["title_pending"] == "Queued title"
    [call] = r.calls
    entries = json.loads(call["prompt"][len(suggest_names.PROMPT):])
    assert entries == [{"wid": "@1", "doing": "Tipstack billing"},
                       {"wid": "@4", "doing": "user: start\nassistant: lint fixes"}]


def test_suggest_model_failure_keeps_current_values(suggest_env):
    got = suggest_names.suggest(["@1", "@4"], _Runner(None, "model call failed (x)"))
    assert got["error"] == "model call failed (x)"
    rows = {row["wid"]: row for row in got["rows"]}
    assert rows["@1"]["suggested_name"] == "tetris"
    assert rows["@1"]["suggested_title"] == "Tetris-style blocks"
    assert rows["@1"]["note"] == "suggestion failed"
    assert rows["@4"]["note"] == "suggestion failed"


def test_suggest_a_row_the_model_skipped(suggest_env):
    got = suggest_names.suggest(["@1"], _Runner({"suggestions": []}))
    [row] = got["rows"]
    assert (row["note"], row["suggested_name"]) == ("no suggestion", "tetris")


def test_suggest_without_content_makes_no_model_call(suggest_env):
    r = _Runner({"suggestions": []})
    got = suggest_names.suggest(["@2"], r)
    assert r.calls == [] and got["error"] is None


# ---------------------------------------------------------------------------
# Typing the title: the gates
# ---------------------------------------------------------------------------

def test_an_idle_empty_claude_pane_gets_the_rename(monkeypatch):
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "idle"}, panes={"@1": GHOST})
    f.tmux.options["@1"] = {suggest_names.PENDING_OPTION: "Tipstack"}
    assert suggest_names.deliver_title("@1", "Tipstack") == "sent"
    assert f.sent == [("@1", "/rename Tipstack", {"interrupt": False})]
    assert f.captures == [("@1", True)]                 # an SGR-aware capture
    assert f.tmux.pending("@1") is None


def test_a_bash_pane_is_never_typed_into(monkeypatch):
    """⛔ The manual run's lesson: a pane that looks like a session but runs bash."""
    f = _Fleet(monkeypatch, pids={}, status={"@3": "idle"}, panes={"@3": EMPTY})
    f.tmux.options["@3"] = {suggest_names.PENDING_OPTION: "Oops"}
    assert suggest_names.deliver_title("@3", "Oops") == "no-claude"
    assert f.sent == [] and f.captures == []
    assert f.tmux.pending("@3") is None                 # dropped, never retried into a shell


@pytest.mark.parametrize("pane", [TRUST, OLD_TRUST, PERMISSION, DIALOG_OVER_EMPTY])
def test_a_pane_showing_a_dialog_is_never_typed_into(monkeypatch, pane):
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "idle"}, panes={"@1": pane})
    f.tmux.options["@1"] = {suggest_names.PENDING_OPTION: "T"}
    assert suggest_names.deliver_title("@1", "T") == "dialog"
    assert f.sent == []
    assert f.tmux.pending("@1") == "T"


@pytest.mark.parametrize("status,want", [
    ("busy", "busy"), ("waiting", "waiting"), (None, "unknown"), ("weird", "unknown")])
def test_a_session_that_is_not_idle_waits(monkeypatch, status, want):
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": status} if status else {},
               panes={"@1": EMPTY})
    f.tmux.options["@1"] = {suggest_names.PENDING_OPTION: "T"}
    assert suggest_names.deliver_title("@1", "T") == want
    assert f.sent == [] and f.captures == []
    assert f.tmux.pending("@1") == "T"


@pytest.mark.parametrize("pane,send_ok,want", [
    ("", True, "unreadable"), (DRAFT, True, "draft"), (EMPTY, False, "failed")])
def test_an_unreadable_pane_a_draft_or_a_failed_send_waits(monkeypatch, pane, send_ok, want):
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "idle"}, panes={"@1": pane}, send_ok=send_ok)
    f.tmux.options["@1"] = {suggest_names.PENDING_OPTION: "T"}
    assert suggest_names.deliver_title("@1", "T") == want
    assert f.sent == ([("@1", "/rename T", {"interrupt": False})] if want == "failed" else [])
    assert f.tmux.pending("@1") == "T"


def test_a_remote_control_name_rename_goes_first(monkeypatch):
    """CMX-39's queued `/rename <name>` lands before the title, never after it."""
    from chela import rc_rename
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "idle"}, panes={"@1": EMPTY})
    f.tmux.options["@1"] = {suggest_names.PENDING_OPTION: "T", rc_rename.PENDING_OPTION: "tipstack"}
    assert suggest_names.deliver_title("@1", "T") == "name-first"
    assert f.sent == [] and f.tmux.pending("@1") == "T"
    del f.tmux.options["@1"][rc_rename.PENDING_OPTION]
    assert suggest_names.deliver_title("@1", "T") == "sent"


def test_a_delivery_never_types_a_title_the_queue_no_longer_holds(monkeypatch):
    """The daemon read the list, the dashboard sent it meanwhile: the flush types nothing."""
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "idle"}, panes={"@1": EMPTY})
    assert suggest_names.deliver_title("@1", "T") == "superseded"           # nothing queued
    f.tmux.options["@1"] = {suggest_names.PENDING_OPTION: "Newer"}
    assert suggest_names.deliver_title("@1", "Older") == "superseded"       # replaced
    assert f.sent == [] and f.tmux.pending("@1") == "Newer"


def test_one_delivery_per_window_at_a_time(monkeypatch):
    f = _Fleet(monkeypatch, pids={"@1": 11, "@2": 12}, status={"@1": "idle", "@2": "idle"},
               panes={"@1": EMPTY, "@2": EMPTY})
    f.tmux.options["@1"] = {suggest_names.PENDING_OPTION: "T"}
    f.tmux.options["@2"] = {suggest_names.PENDING_OPTION: "U"}
    with suggest_names._claim("@1") as held:
        assert held is True
        assert suggest_names.deliver_title("@1", "T") == "locked"
        assert suggest_names.deliver_title("@2", "U") == "sent"        # another window is free
    assert f.sent == [("@2", "/rename U", {"interrupt": False})]
    assert suggest_names.deliver_title("@1", "T") == "sent"             # released


def test_a_title_tmux_will_not_queue_is_refused_not_reported_pending(monkeypatch, client):
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "busy"}, panes={"@1": EMPTY})
    f.tmux.fail_set = True
    assert suggest_names.apply_title("@1", "-wip refactor") == {
        "ok": False, "state": "refused", "reason": "could not queue the title (tmux refused it)"}
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@1": "a"})
    r = client.post("/api/agents/@1/title", json={"title": "x"})
    assert r.status_code == 500 and r.get_json()["reason"] == suggest_names.NOT_QUEUED
    assert f.sent == []


def test_get_pending_unknown_reads_as_nothing_queued(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("tmux", 5)
    monkeypatch.setattr(subprocess, "run", boom)
    assert suggest_names._get_pending("@1") is None
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="T\n", stderr=""))
    assert suggest_names._get_pending("@1") is None


def test_a_dialog_quoted_high_up_in_the_scrollback_does_not_hold_the_queue():
    old = "Do you want to proceed?\n" + "".join(f"line {i}\n" for i in range(25)) + "❯ \n"
    assert suggest_names.dialog_showing(old) is None
    assert suggest_names.dialog_showing("Do you want to proceed?\n\n\n" + "x\n" * 18 + "❯ \n") == "permission"


@pytest.mark.parametrize("pane,want", [
    ("> 1. a quoted list item\n❯ \n", None),
    (TRUST, "trust"), (OLD_TRUST, "trust"), (PERMISSION, "permission"),
    (" Select model\n › 2. Sonnet\n", "menu"), ("Enter to select · Esc to cancel", "menu"),
    ("Do you want to make this edit to a.py?", "permission"),
    ("❯ \n", None), ("✻ Thinking… (12s)\n❯ \n", None),
    ("I will trust the tests here. The folder looks fine.\n❯ \n", None),
    ("1. first point\n2. second\n❯ \n", None),
])
def test_dialog_showing_fixtures(pane, want):
    assert suggest_names.dialog_showing(pane) == want


def test_a_busy_sessions_title_waits_and_applies_on_idle(monkeypatch):
    """Acceptance: queued while busy, shown pending, typed by the daemon's flush once idle."""
    f = _Fleet(monkeypatch, pids={"@1": 11, "@2": 12}, status={"@1": "busy", "@2": "idle"},
               panes={"@1": EMPTY, "@2": TRUST})
    assert suggest_names.apply_title("@1", "Tipstack billing") == {
        "ok": True, "state": "pending", "reason": "busy"}
    assert suggest_names.apply_title("@2", "Trust blocked") == {
        "ok": True, "state": "pending", "reason": "dialog"}
    assert suggest_names.pending_titles() == {"@1": "Tipstack billing", "@2": "Trust blocked"}
    assert suggest_names.flush_pending() == {"@1": "busy", "@2": "dialog"}
    assert f.sent == []
    f.status["@1"] = "idle"
    assert suggest_names.flush_pending() == {"@1": "sent", "@2": "dialog"}
    assert f.sent == [("@1", "/rename Tipstack billing", {"interrupt": False})]
    assert suggest_names.pending_titles() == {"@2": "Trust blocked"}


def test_apply_title_now_and_refusals(monkeypatch):
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "idle"}, panes={"@1": EMPTY})
    assert suggest_names.apply_title("@1", "  Tipstack\nbilling ") == {
        "ok": True, "state": "applied", "reason": None}
    assert f.sent == [("@1", "/rename Tipstack billing", {"interrupt": False})]
    assert suggest_names.apply_title("@1", "\x1b\x07") == {
        "ok": False, "state": "refused", "reason": suggest_names.BAD_TITLE}
    assert suggest_names.apply_title("@3", "Shell") == {
        "ok": False, "state": "refused", "reason": "no Claude session runs in this pane"}
    assert len(f.sent) == 1 and f.tmux.pending("@3") is None


def test_apply_title_when_claude_exits_between_checks(monkeypatch):
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "idle"}, panes={"@1": EMPTY})
    calls = iter([11, None])
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: next(calls))
    assert suggest_names.apply_title("@1", "T") == {
        "ok": False, "state": "refused", "reason": suggest_names.NO_CLAUDE}
    assert f.sent == [] and f.tmux.pending("@1") is None


def test_pending_titles_fail_closed_to_nothing(monkeypatch):
    tmux = _FakeTmux({"@1": {suggest_names.PENDING_OPTION: "T"}}, fail_list=True)
    monkeypatch.setattr(subprocess, "run", tmux)
    assert suggest_names.pending_titles() == {}

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("tmux", 5)
    monkeypatch.setattr(subprocess, "run", boom)
    assert suggest_names.pending_titles() == {}
    suggest_names._set_pending("@1", "T")   # logs, never raises


def test_pending_titles_argv(monkeypatch):
    tmux = _FakeTmux({"@1": {suggest_names.PENDING_OPTION: "T"}, "@2": {}})
    monkeypatch.setattr(subprocess, "run", tmux)
    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")
    assert suggest_names.pending_titles() == {"@1": "T"}
    assert tmux.calls == [["tmux", "list-windows", "-t", "chela", "-F",
                           "#{window_id}\t#{@chela_title_pending}"]]
    suggest_names._set_pending("@2", "Next")
    suggest_names._set_pending("@1", None)
    assert tmux.calls[1:] == [["tmux", "set-window-option", "-t", "chela:@2", "@chela_title_pending", "Next"],
                              ["tmux", "set-window-option", "-u", "-t", "chela:@1", "@chela_title_pending"]]
    assert tmux.kwargs == [{"capture_output": True, "text": True, "timeout": 5}] * 3


def test_titles_reach_custom_title(monkeypatch, tmp_path):
    """Acceptance: the typed `/rename` is what lands as the transcript's customTitle. The
    fake session records it the way Claude Code does, and the next read shows it."""
    p = _transcript(tmp_path, [{"type": "ai-title", "aiTitle": "Tetris-style blocks"}])
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "idle"}, panes={"@1": EMPTY})

    def claude_code(wid, text, **kw):
        f.sent.append((wid, text, kw))
        assert text.startswith("/rename ")
        with p.open("a") as fh:
            fh.write(json.dumps({"type": "custom-title", "customTitle": text[len("/rename "):]}) + "\n")
        return True
    monkeypatch.setattr(messenger, "send_tmux", claude_code)
    assert suggest_names.apply_title("@1", "Tipstack")["state"] == "applied"
    assert suggest_names.current_title(p) == "Tipstack"


# ---------------------------------------------------------------------------
# The HTTP surface
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return dash.app.test_client()


def test_title_route(monkeypatch, client):
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@1": "a", "@3": "sh"})
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "busy"}, panes={"@1": EMPTY})
    r = client.post("/api/agents/@1/title", json={"title": "Tipstack"})
    assert (r.status_code, r.get_json()) == (200, {"ok": True, "state": "pending", "reason": "busy"})
    r = client.post("/api/agents/@3/title", json={"title": "Shell"})
    assert r.status_code == 409 and r.get_json()["reason"] == suggest_names.NO_CLAUDE
    r = client.post("/api/agents/@9/title", json={"title": "x"})
    assert r.status_code == 404
    for body in ({}, {"title": "  "}, {"title": 3}):
        assert client.post("/api/agents/@1/title", json=body).status_code == 400
    r = client.post("/api/agents/@1/title", json={"title": "\x07\x1b"})
    assert r.status_code == 400 and r.get_json()["reason"] == suggest_names.BAD_TITLE
    assert f.sent == []


def test_suggest_route(monkeypatch, client):
    seen = []
    monkeypatch.setattr(suggest_names, "suggest", lambda wids: seen.append(wids) or {"rows": [], "error": None})
    r = client.post("/api/agents/suggest-names", json={"wids": ["@1", "@2"]})
    assert (r.status_code, r.get_json(), seen) == (200, {"ok": True, "rows": [], "error": None}, [["@1", "@2"]])
    for body in ({}, {"wids": "@1"}, {"wids": [1]}):
        assert client.post("/api/agents/suggest-names", json=body).status_code == 400
    assert seen == [["@1", "@2"]]


def _agents_route(client, panes):
    from unittest.mock import patch
    summary = {"recap": None, "recap_ts": None, "pr": None, "ai_title": "ai",
               "custom_title": "Tipstack", "last_activity": None}
    with (
        patch("chela.discovery.get_all_windows", return_value={"o": "@1", "w": "@9"}),
        patch("chela.dispatcher.list_runs", return_value=[]),
        patch("chela.agent_manager.session_status_map", return_value={"by_pid": {}}),
        patch("chela.agent_manager.claude_pid", return_value=None),
        patch("chela.agent_manager.window_type", return_value="shell"),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary", return_value=summary),
        patch("chela.messenger.capture_pane", return_value=""),
        patch("chela.sessions.panes", side_effect=lambda force=False: panes),
    ):
        return {a["window_id"]: a for a in client.get("/api/agents").get_json()}


def test_api_agents_rows_carry_custom_title_and_pending_without_a_spawn(monkeypatch, client):
    """The pending title rides the shared pane snapshot: /api/agents spawns nothing for it."""
    spawned = []
    monkeypatch.setattr(suggest_names, "pending_titles", lambda: spawned.append(1) or {})
    rows = _agents_route(client, {"@1": sessions.Pane(wid="@1"),
                                  "@9": sessions.Pane(wid="@9", title_pending="Queued")})
    assert rows["@1"]["custom_title"] == "Tipstack" and rows["@1"]["title_pending"] is None
    assert rows["@9"]["title_pending"] == "Queued"
    assert spawned == []


def test_pending_title_reads_the_pane_snapshot(monkeypatch):
    from chela import probecache
    monkeypatch.setattr(sessions, "panes", lambda force=False: {"@1": sessions.Pane(wid="@1", title_pending="T")})
    assert suggest_names.pending_title("@1") == "T"
    assert suggest_names.pending_title("@2") is None
    with probecache.batch():
        assert suggest_names.pending_title("@1") == "T"
        assert suggest_names.pending_title("@2") is None


@pytest.mark.parametrize("stdout,want", [
    ("@1\tbash\t/a\t1\t1\tTipstack billing\n@2\tbash\t/b\t2\t\t\n", {"@1": "Tipstack billing", "@2": None}),
    ("@1\tbash\t/a\t1\t1\n", {"@1": None}),                 # a 5-column row: nothing pending
    ("@1\tbash\t/a\t1\t1\tx\textra\n", {}),                 # a malformed row is skipped
])
def test_the_pane_map_reads_the_pending_title_column(tmp_path, monkeypatch, stdout, want):
    monkeypatch.setattr(sessions, "PROC", tmp_path / "nonexistent")
    seen = []

    def run(argv, **kw):
        seen.append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(sessions.subprocess, "run", run)
    panes = sessions._load_panes()
    assert {w: p.title_pending for w, p in panes.items()} == want
    assert seen[0][-1].endswith("\t#{@chela_manual_name}\t#{@chela_title_pending}")


# ---------------------------------------------------------------------------
# CMX-459: "Rename title…" (the pane and row ⋯ menus) and the pane header's double-click
# on the title set ONE session's title through the SAME route as the dialog's apply, so
# CMX-434's safety cases hold for it unchanged. These drive that route, as the menu does.
# ---------------------------------------------------------------------------

def _retitle(client, wid, title):
    r = client.post(f"/api/agents/{wid}/title", json={"title": title})
    return r.status_code, r.get_json()


def test_rename_title_on_an_idle_session_applies_at_once_and_reaches_custom_title(monkeypatch, client, tmp_path):
    """Acceptance: idle ⇒ typed now; the transcript gets a `custom-title` the row then reads."""
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@1": "rally-proto"})
    p = _transcript(tmp_path, [{"type": "ai-title", "aiTitle": "Rally design milestone 1"}])
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "idle"}, panes={"@1": GHOST})

    def claude_code(wid, text, **kw):
        f.sent.append((wid, text, kw))
        with p.open("a") as fh:
            fh.write(json.dumps({"type": "custom-title", "customTitle": text[len("/rename "):]}) + "\n")
        return True
    monkeypatch.setattr(messenger, "send_tmux", claude_code)
    assert _retitle(client, "@1", "  Rally milestone 2 ") == (200, {"ok": True, "state": "applied", "reason": None})
    assert f.sent == [("@1", "/rename Rally milestone 2", {"interrupt": False})]
    assert suggest_names.current_title(p) == "Rally milestone 2"
    assert f.tmux.pending("@1") is None


def test_rename_title_on_a_bash_pane_is_refused_and_never_typed(monkeypatch, client):
    """Acceptance: a pane with no Claude in it refuses (409); nothing typed, nothing queued."""
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@3": "sh"})
    f = _Fleet(monkeypatch, pids={}, status={}, panes={"@3": BASH})
    assert _retitle(client, "@3", "Oops") == (
        409, {"ok": False, "state": "refused", "reason": suggest_names.NO_CLAUDE})
    assert f.sent == [] and f.captures == []
    assert f.tmux.pending("@3") is None


@pytest.mark.parametrize("pane,why", [
    (TRUST, "dialog"), (PERMISSION, "dialog"), (DIALOG_OVER_EMPTY, "dialog"), (DRAFT, "draft")])
def test_rename_title_never_types_over_a_dialog_or_a_typed_draft(monkeypatch, client, pane, why):
    """CMX-434's safety cases through the menu's route: queued (pending), never typed."""
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@1": "a"})
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "idle"}, panes={"@1": pane})
    assert _retitle(client, "@1", "Rally 2") == (200, {"ok": True, "state": "pending", "reason": why})
    assert f.sent == []
    assert f.tmux.pending("@1") == "Rally 2"


def test_rename_title_on_a_busy_session_is_pending_and_applies_on_idle(monkeypatch, client):
    """Acceptance: busy ⇒ pending (the row's chip reads the queue), typed by the flush on idle."""
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@1": "a"})
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "busy"}, panes={"@1": EMPTY})
    assert _retitle(client, "@1", "Rally 2") == (200, {"ok": True, "state": "pending", "reason": "busy"})
    assert f.sent == [] and suggest_names.pending_titles() == {"@1": "Rally 2"}
    assert suggest_names.flush_pending() == {"@1": "busy"}
    f.status["@1"] = "idle"
    assert suggest_names.flush_pending() == {"@1": "sent"}
    assert f.sent == [("@1", "/rename Rally 2", {"interrupt": False})]
    assert suggest_names.pending_titles() == {}
