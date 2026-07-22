"""``chela update`` (CMX-142 part 1) — the human-run half of self-update.

Everything here runs against REAL local git repos (a real `upstream` + a real clone
tracking it), not mocked git — the safety rail this module exists for (never clobber a
dirty tree, never force a merge past a diverged branch) has to survive real git's actual
behaviour, not a mock's idea of it. Only the non-git subprocesses (`uv sync`, `pm2`) are
stubbed, since neither is guaranteed to exist in a test sandbox and neither should ever
actually run here.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
from pathlib import Path

import pytest

from chela import main, update


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _configure(repo: Path) -> None:
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(repo, "config", k, v)


def _commit(repo: Path, filename: str, content: str, message: str = "update") -> None:
    (repo / filename).write_text(content)
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """A real 'upstream' repo with one commit on main."""
    repo = tmp_path / "upstream"
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _configure(repo)
    _commit(repo, "README.md", "seed\n", "seed")
    return repo


@pytest.fixture
def checkout(upstream: Path, tmp_path: Path) -> Path:
    """A real clone of `upstream`, tracking it — what chela.update actually operates on."""
    repo = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(upstream), str(repo)], check=True, capture_output=True)
    _configure(repo)
    return repo


@pytest.fixture
def git_calls(monkeypatch):
    """Spies on every `update._git` call as (subcommand, full_args) — real git still runs."""
    calls: list[tuple[str, tuple]] = []
    real_git = update._git

    def spy(repo, *args, **kwargs):
        calls.append(args)
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(update, "_git", spy)
    return calls


class _FakeCP:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --- dirty tree ⇒ refuse -------------------------------------------------------------

def test_dirty_tree_refuses_and_never_pulls(checkout, upstream, git_calls):
    _commit(upstream, "new.txt", "new\n")           # something IS available to pull
    (checkout / "README.md").write_text("dirty, uncommitted edit\n")

    result = update.apply(checkout)

    assert result.ok is False
    assert result.step == "dirty-check"
    assert not any(args and args[0] == "pull" for args in git_calls)


def test_a_clean_tree_with_nothing_behind_never_calls_pull_either(checkout, git_calls):
    """Sanity: the dirty-check itself must actually run `git status`, not just always pass."""
    result = update.apply(checkout)
    assert result.ok is True
    assert result.behind_before == 0
    assert any(args and args[0] == "status" for args in git_calls)


# --- not fast-forward ⇒ refuse ---------------------------------------------------------

def test_diverged_branch_refuses_and_never_pulls(checkout, upstream, git_calls):
    _commit(upstream, "new.txt", "new\n")            # upstream moves...
    _commit(checkout, "local.txt", "local\n")         # ...and so does the local checkout

    result = update.apply(checkout)

    assert result.ok is False
    assert result.step == "ff-check"
    assert not any(args and args[0] == "pull" for args in git_calls)


# --- happy path order --------------------------------------------------------------

def test_happy_path_fetches_then_pulls_ff_only_then_syncs_then_restarts(
    checkout, upstream, git_calls, monkeypatch,
):
    _commit(upstream, "new.txt", "new\n")

    sh_kinds: list[str] = []

    def fake_sh(args, cwd, timeout=update._SHELL_TIMEOUT_SECONDS):
        if args[0] == "pm2" and args[1] == "jlist":
            sh_kinds.append("pm2-query")
            return _FakeCP(stdout="[]")               # nothing running -> nothing to restart
        if args[:2] == ["uv", "sync"]:
            sh_kinds.append("uv-sync")
            return _FakeCP()
        raise AssertionError(f"unexpected _sh call: {args}")

    monkeypatch.setattr(update, "_sh", fake_sh)

    result = update.apply(checkout)

    assert result.ok is True
    assert result.behind_before == 1
    assert result.restarted == []

    git_kinds = [args[0] for args in git_calls if args and args[0] in ("fetch", "pull")]
    assert git_kinds == ["fetch", "pull"]
    pull_args = next(args for args in git_calls if args and args[0] == "pull")
    assert "--ff-only" in pull_args, "dropping --ff-only would let a non-ff merge through"
    assert sh_kinds == ["uv-sync", "pm2-query"]


def test_happy_path_restarts_only_running_chela_services(checkout, upstream, monkeypatch):
    _commit(upstream, "new.txt", "new\n")
    restart_calls = []

    def fake_sh(args, cwd, timeout=update._SHELL_TIMEOUT_SECONDS):
        if args[:2] == ["pm2", "jlist"]:
            return _FakeCP(stdout='[{"name": "chela-daemon", "pm2_env": {"status": "online"}}, '
                                   '{"name": "chela-dashboard", "pm2_env": {"status": "stopped"}}, '
                                   '{"name": "unrelated-app", "pm2_env": {"status": "online"}}]')
        if args[:2] == ["uv", "sync"]:
            return _FakeCP()
        if args[0] == "pm2" and args[1] == "restart":
            restart_calls.append(args)
            return _FakeCP()
        raise AssertionError(f"unexpected _sh call: {args}")

    monkeypatch.setattr(update, "_sh", fake_sh)

    result = update.apply(checkout)

    assert result.ok is True
    assert result.restarted == ["chela-daemon"]       # not the stopped one, not the unrelated one
    assert restart_calls == [["pm2", "restart", "chela-daemon"]]


# --- `--check` is read-only ----------------------------------------------------------

def test_commits_behind_is_read_only(checkout, upstream, git_calls, monkeypatch):
    _commit(upstream, "new.txt", "new\n")
    sh_calls = []
    monkeypatch.setattr(update, "_sh", lambda *a, **k: sh_calls.append(a))

    status = update.commits_behind(checkout)

    assert status.ok is True
    assert status.behind == 1
    assert status.ahead == 0
    assert sh_calls == []
    assert not any(args and args[0] == "pull" for args in git_calls)


def test_commits_behind_reports_no_upstream_without_erroring(tmp_path):
    repo = tmp_path / "solo"
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _configure(repo)
    _commit(repo, "README.md", "seed\n", "seed")

    status = update.commits_behind(repo)

    assert status.ok is True
    assert status.behind == 0
    assert "upstream" in status.error


def test_cli_check_flag_never_calls_apply(checkout, upstream, monkeypatch):
    _commit(upstream, "new.txt", "new\n")
    monkeypatch.setattr(update, "repo_root", lambda: checkout)
    monkeypatch.setattr(
        update, "apply",
        lambda *a, **k: pytest.fail("chela update --check must never call apply()"),
    )

    main.cmd_update(argparse.Namespace(check=True))


def test_cli_without_check_does_call_apply(checkout, monkeypatch):
    monkeypatch.setattr(update, "repo_root", lambda: checkout)
    called = []
    monkeypatch.setattr(update, "apply", lambda repo: called.append(repo) or
                         update.ApplyResult(ok=True, step="done", behind_before=0))

    main.cmd_update(argparse.Namespace(check=False))

    assert called == [checkout]


# --- notifier is edge-triggered + never pulls -----------------------------------------

class _StubNotify:
    def __init__(self, enabled: bool):
        self._enabled = enabled
        self.sent: list[tuple] = []

    def enabled(self):
        return self._enabled

    def send(self, message, title=None):
        self.sent.append((message, title))
        return True


def _tick(repo, previously_behind, monkeypatch):
    """check_and_notify() targets repo_root(); point it at our fixture repo instead."""
    monkeypatch.setattr(update, "repo_root", lambda: repo)
    return update.check_and_notify(previously_behind)


def test_notifier_never_pulls(checkout, upstream, git_calls, monkeypatch):
    _commit(upstream, "new.txt", "new\n")
    monkeypatch.setattr(update, "notify", _StubNotify(enabled=False))
    before_head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    behind = _tick(checkout, 0, monkeypatch)

    assert behind == 1
    assert not any(args and args[0] in ("pull", "merge") for args in git_calls)
    after_head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert after_head == before_head                  # HEAD never moved


def test_notifier_logs_and_notifies_exactly_once_across_repeated_ticks(checkout, upstream, monkeypatch, caplog):
    _commit(upstream, "new.txt", "new\n")
    stub = _StubNotify(enabled=True)
    monkeypatch.setattr(update, "notify", stub)

    with caplog.at_level(logging.WARNING, logger=update.log.name):
        behind = _tick(checkout, 0, monkeypatch)
        behind = _tick(checkout, behind, monkeypatch)   # a second tick, still behind by the same amount

    assert behind == 1
    assert len(stub.sent) == 1                          # not one per tick
    assert sum(1 for r in caplog.records if "update available" in r.getMessage()) == 1


def test_notifier_stays_quiet_when_nothing_is_behind(checkout, monkeypatch):
    stub = _StubNotify(enabled=True)
    monkeypatch.setattr(update, "notify", stub)

    behind = _tick(checkout, 0, monkeypatch)

    assert behind == 0
    assert stub.sent == []


# --- repo_root ------------------------------------------------------------------------

def test_repo_root_refuses_a_non_git_directory(tmp_path, monkeypatch):
    plain_dir = tmp_path / "not_a_repo"
    plain_dir.mkdir()
    monkeypatch.setattr(update, "__file__", str(plain_dir / "fake_pkg" / "update.py"))
    (plain_dir / "fake_pkg").mkdir()

    with pytest.raises(update.NotAGitCheckout):
        update.repo_root()
