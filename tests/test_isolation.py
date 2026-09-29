"""The suite must not be able to touch the developer's real install — prove it.

On 2026-07-14 the real ``~/.chela/events.jsonl`` was found holding 43 synthetic
``hook.permission_request`` events: fixture ``session_id="s1"``, a ``-repo`` transcript
slug, in duplicate pairs. Test data, in production. ``tests/conftest.py`` isolated
``CHELA_ENV_FILE`` and ``CLAUDE_CONFIG_DIR`` but never ``CHELA_DIR``, so
``event_log.log_path()`` resolved to the developer's own log and every test that appended
an event appended it *there*.

That was the seventh instance of one bug class in a day (CMX-33, CMX-46, CMX-56 …), which
makes it a missing mechanism rather than seven mistakes. These tests are the mechanism:
they assert the state paths land in a scratch dir, and — the part a future "simplifying"
refactor cannot quietly undo — that the guard **fails** when code does reach the real dir.
Delete conftest's isolation and this file goes red.
"""
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from chela import config, event_log, gateanswer, hold, inbox
# ``conftest``, not ``tests.conftest``: tests/ has no __init__.py, so pytest imports the
# conftest as a TOP-LEVEL module. Spelling it the other way imports a SECOND copy — with a
# second sandbox and a second exception class — and every assertion below silently passes
# against the wrong module.
from conftest import (
    REAL_CHELA_DIRS, REAL_HOME_CHELA_DIRS, SANDBOX_CHELA_DIR, LiveChelaDirEscape,
    LiveStateEscape, _refuse_real_chela_dir,
)

REAL_CHELA_DIR = REAL_CHELA_DIRS[0]
REAL_LOG = REAL_CHELA_DIR / "events.jsonl"


# --- the redirect: every state path lands in a scratch dir ---------------------------

def test_chela_dir_is_a_scratch_dir_not_the_developers(tmp_path):
    assert config.CHELA_DIR == tmp_path / ".chela"
    for real in REAL_CHELA_DIRS:
        assert config.CHELA_DIR != real
        assert real not in config.CHELA_DIR.parents


@pytest.mark.parametrize("resolve, name", [
    (lambda: event_log.log_path(), "events.jsonl"),
    (lambda: inbox.store_path(), "inbox.json"),
    (lambda: gateanswer.gates_dir(), "gates"),
    (lambda: hold.path(), "dispatch-hold.json"),
    (lambda: config.dashboard_port_file(), "dashboard.port"),
])
def test_every_state_path_resolves_into_the_scratch_dir(resolve, name, tmp_path):
    """The writers that escaped, and their neighbours. Each is resolved PER CALL — a
    module that latches ``CHELA_DIR`` at import binds the value its importing process saw
    first, and no fixture can reach it afterwards. That latch *was* the bug."""
    path = resolve()
    assert path == tmp_path / ".chela" / name
    assert REAL_CHELA_DIR not in path.parents


def test_the_import_time_latches_are_never_the_developers_state():
    """``dispatcher.DB_PATH`` / ``scheduler.DB_PATH`` / ``launcher._STORE`` /
    ``config.CONTEXT_CACHE_DIR`` are computed at *import* — before any fixture exists,
    which is precisely why conftest redirects ``CHELA_DIR`` at its own import rather than
    in a fixture. Whichever scratch dir they latched (the session sandbox, or a per-test
    one if the module was first imported inside a test), the invariant is the same: never
    the developer's runs DB. CMX-33 was that DB, read live — green only on an idle
    machine."""
    from chela import dispatcher, launcher, scheduler

    sandboxes = (SANDBOX_CHELA_DIR, Path(tempfile.gettempdir()))
    for latched in (dispatcher.DB_PATH, scheduler.DB_PATH, launcher._STORE,
                    config.CONTEXT_CACHE_DIR):
        assert any(box in latched.parents for box in sandboxes), latched
        for real in REAL_CHELA_DIRS:
            assert real not in latched.parents


def test_appending_an_event_writes_the_scratch_log_and_not_the_real_one(tmp_path):
    event_log.append("test.synthetic", summary="s1", wid="@3")
    written = (tmp_path / ".chela" / "events.jsonl").read_text(encoding="utf-8")
    assert "test.synthetic" in written
    assert event_log.log_path() != REAL_LOG


# --- the fence: reaching the real dir FAILS the test ----------------------------------

def test_writing_the_real_event_log_fails_the_test():
    """The guard, doing the one job it exists for. Without it this call silently appends
    to production, which is precisely what happened for weeks."""
    with pytest.raises(LiveStateEscape, match="LIVE chela state"):
        open(REAL_LOG, "a")


def test_reading_the_real_dir_fails_too():
    """Reads, not just writes: CMX-33/46/56 were all *reads* of live state — a test that
    reads the machine it runs on is green by luck, not by correctness."""
    with pytest.raises(LiveStateEscape):
        open(REAL_CHELA_DIR / "chela.env")


@pytest.mark.parametrize("door", ["builtins", "io", "os", "pathlib"])
def test_every_door_into_the_real_dir_is_guarded(door):
    """``Path.write_text`` does not go through ``builtins.open`` — it goes through
    ``io.open``, a *separate reference* to the same function. Guarding one door and
    calling it done is how this class of bug survives."""
    with pytest.raises(LiveStateEscape):
        if door == "builtins":
            open(REAL_LOG, "a")
        elif door == "io":
            io.open(REAL_LOG, "a")
        elif door == "os":
            os.open(str(REAL_LOG), os.O_APPEND | os.O_WRONLY)
        else:
            (REAL_CHELA_DIR / "inbox.json").write_text("{}", encoding="utf-8")


def test_the_guard_cannot_be_swallowed_by_a_writer_that_catches_Exception(monkeypatch):
    """``event_log.append`` swallows ``Exception`` on purpose — a hook that raises would
    stall a live agent. So the guard raises ``BaseException``: a fence a writer can catch
    is not a fence, and this is the exact writer that escaped."""
    monkeypatch.setattr(config, "CHELA_DIR", REAL_CHELA_DIR)
    assert event_log.log_path() == REAL_LOG          # aimed straight at production…
    with pytest.raises(LiveStateEscape):             # …and stopped anyway
        event_log.append("hook.permission_request", summary="fake", session_id="s1")


def test_an_agent_worktree_is_source_not_live_state(tmp_path):
    """A dispatched agent's worktree lives under ``~/.chela/worktrees`` — this very
    checkout may be one. Guarding it would make the suite unable to read its own source,
    so the subtree is exempt; only chela's *state* is fenced."""
    worktree_file = REAL_CHELA_DIR / "worktrees" / "nothing-here.txt"
    with pytest.raises(FileNotFoundError):           # reached the filesystem, not the fence
        open(worktree_file)


# --- CMX-391: the runs DB, and a CHELA_DIR that IS the real one --------------------------

REAL_HOME_CHELA = REAL_HOME_CHELA_DIRS[0]


@pytest.fixture
def stand_in_live(tmp_path, monkeypatch):
    """The fence, aimed at a STAND-IN live dir. A test proving the fence must not use the
    real ``~/.chela`` as its target: under the self-check's corruption (fence removed) the
    connect — and ``dispatcher._db()``'s schema migration — would then run for real against
    the operator's runs DB. The fence reads ``conftest.REAL_CHELA_DIRS`` per call, so
    re-pointing it exercises the exact code path at a harmless address."""
    import conftest

    live = tmp_path / "live-chela"
    live.mkdir()
    monkeypatch.setattr(conftest, "REAL_CHELA_DIRS", (live,))
    monkeypatch.setattr(conftest, "_EXEMPT", (live / "worktrees",))
    return live


@pytest.mark.parametrize("form", ["str", "path", "uri"])
def test_opening_the_live_runs_db_fails_the_test(stand_in_live, form):
    """🔴 GUARD — ``sqlite3.connect`` opens its file in C, past every Python ``open`` door,
    and ``scheduler.db`` is the one file the daemon ACTS on. Corrupt the fence (drop the
    ``sqlite3.connect`` patch) ⇒ this connects ⇒ RED."""
    db = stand_in_live / "scheduler.db"
    target = {"str": str(db), "path": db, "uri": f"file:{db}?mode=rwc"}[form]
    with pytest.raises(LiveStateEscape, match="LIVE chela database"):
        sqlite3.connect(target, uri=True)
    assert not db.exists()


def test_the_dispatcher_cannot_reach_the_live_runs_db(stand_in_live, monkeypatch):
    """The same fence, through the product code that writes run rows."""
    from chela import dispatcher

    monkeypatch.setattr(dispatcher, "DB_PATH", stand_in_live / "scheduler.db")
    with pytest.raises(LiveStateEscape):
        with dispatcher._db():
            pass
    assert not (stand_in_live / "scheduler.db").exists()


def test_a_scratch_and_an_in_memory_db_still_open(tmp_path):
    """⭐ The accept case: the fence stops the live DB, not sqlite."""
    sqlite3.connect(":memory:").close()
    sqlite3.connect(tmp_path / "x.db").close()
    sqlite3.connect(f"file:{tmp_path / 'y.db'}", uri=True).close()


def test_a_chela_dir_that_resolves_to_the_real_home_one_is_refused(tmp_path):
    """🔴 GUARD — the check the per-test autouse fixture and conftest's import both run.
    Corrupt it (compare against the wrong dir, or return early) ⇒ RED."""
    with pytest.raises(LiveChelaDirEscape, match="REAL chela dir"):
        _refuse_real_chela_dir(REAL_HOME_CHELA)
    with pytest.raises(LiveChelaDirEscape):
        _refuse_real_chela_dir(str(REAL_HOME_CHELA / "worktrees" / ".."))
    _refuse_real_chela_dir(tmp_path / ".chela")          # a scratch dir is fine
    _refuse_real_chela_dir(None)


def test_every_test_runs_behind_the_real_chela_dir_check(tmp_path):
    """The per-test fixture, as a test sees it: this very test's env and config are the
    scratch dir, and would have been refused otherwise."""
    assert os.environ["CHELA_DIR"] == str(tmp_path / ".chela")
    assert config.CHELA_DIR not in REAL_HOME_CHELA_DIRS


def test_a_suite_pointed_at_the_real_dir_refuses_to_run(tmp_path):
    """End to end: a child pytest whose conftest redirect is DISABLED (so CHELA_DIR stays
    the operator's real one) must stop before running a test — never against live state.
    Runs a throwaway suite that imports this conftest with its redirect neutralised."""
    root = Path(__file__).resolve().parent
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "conftest.py").write_text(
        "import importlib.util, os, sys\n"
        f"sys.path.insert(0, {str(root.parent)!r})\n"
        f"src = open({str(root / 'conftest.py')!r}).read()\n"
        "src = src.replace('os.environ[\"CHELA_DIR\"] = str(SANDBOX_CHELA_DIR)', 'pass')\n"
        "exec(compile(src, 'conftest_under_test', 'exec'))\n"
    )
    (suite / "test_x.py").write_text("def test_x():\n    pass\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CHELA_")}
    env["CHELA_DIR"] = str(REAL_HOME_CHELA)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
         "--rootdir", str(suite), str(suite)],
        cwd=str(suite), env=env, capture_output=True, text=True, timeout=120,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "LiveChelaDirEscape" in out and "1 passed" not in out, out
