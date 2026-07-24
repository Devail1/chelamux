"""``chela update`` — self-update, in two halves: CMX-142 part 1 (human-run `chela update`
+ the behind-notifier) and CMX-148 part 2 (`CHELA_AUTO_UPDATE`'s unattended sweep).

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
from types import SimpleNamespace

import pytest

from chela import config, main, update


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


# --- history rewrite (force-push scrub) ⇒ recover safely, never refuse -----------------

def _force_rewrite_history(upstream: Path) -> None:
    """Simulate exactly what `git filter-repo` + a force-push does: replace `main` with a
    brand-new, unrelated commit history (same eventual content, different commit objects
    all the way down to a new root). A real orphan branch, not a mock -- the whole point
    of this module's safety rail is that it must survive real git's actual behaviour.
    """
    _git(upstream, "checkout", "-q", "--orphan", "scrubbed")
    (upstream / "README.md").write_text("scrubbed\n")
    _git(upstream, "add", "README.md")
    _configure(upstream)
    _git(upstream, "commit", "-q", "-m", "scrubbed history")
    _git(upstream, "branch", "-M", "scrubbed", "main")


def test_history_rewrite_is_recovered_safely_not_refused(checkout, upstream, monkeypatch):
    """🔴 THE LOAD-BEARING GUARD for CMX-168. A force-pushed history rewrite must NOT hit
    the old "diverged, not fast-forwardable; resolve by hand" dead end -- it must recover:
    back up the pre-rewrite HEAD, then land exactly on the new upstream tip."""
    before_head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    _force_rewrite_history(upstream)
    monkeypatch.setattr(update, "_sh", _pm2_stub([]))

    result = update.apply(checkout)

    assert result.ok is True
    assert result.rewrite_recovered is True
    assert result.step != "ff-check"
    assert result.backup_ref.startswith("refs/chela-backup/")

    new_head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    upstream_head = subprocess.run(
        ["git", "-C", str(upstream), "rev-parse", "main"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert new_head == upstream_head, "must land exactly on the rewritten upstream tip"

    backed_up = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", result.backup_ref],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert backed_up == before_head, "the pre-rewrite HEAD must be preserved, not lost"


def test_partial_rewrite_with_surviving_ancestor_is_recovered(checkout, upstream, monkeypatch):
    """🔴 THE REALISTIC scrub — the case a `--orphan` test misses. A mid-history
    `git filter-repo` keeps every commit BEFORE the first change unmodified, so the old and
    rewritten histories still SHARE a common ancestor (only a full --orphan rewrite drops
    it). Detection must key on the non-fast-forward move of the remote-tracking ref, NOT on
    "HEAD and @{u} share no common ancestor" — otherwise this exact case refuses and the
    self-heal never fires for the rewrite it exists for. Reproduces the shape observed
    against the real repo (ahead>0, behind>0, common ancestor survives).
    """
    # Give upstream a shared base and bring the checkout fully up to date on the OLD tip
    # (leave its remote-tracking ref STALE — apply() reads it pre-fetch to spot the rewrite).
    _commit(upstream, "f1.txt", "v1\n", "c1")
    subprocess.run(["git", "-C", str(checkout), "pull", "-q", "--ff-only"],
                   check=True, capture_output=True)
    before_head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    # Force-push a rewrite that orphans that tip but keeps the seed as a shared ancestor:
    # reset back one commit and re-commit — a mid-history filter-repo in miniature.
    _git(upstream, "reset", "--hard", "HEAD~1")
    _commit(upstream, "f1.txt", "v2\n", "c1 rewritten")
    upstream_new = subprocess.run(
        ["git", "-C", str(upstream), "rev-parse", "main"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    # PROVE this is the partial case: a common ancestor still exists between the old
    # checkout HEAD and the rewritten upstream tip (what the "no common ancestor" test missed).
    assert subprocess.run(
        ["git", "-C", str(upstream), "merge-base", before_head, upstream_new],
        capture_output=True,
    ).returncode == 0, "test must exercise the shared-ancestor (partial-rewrite) case"

    monkeypatch.setattr(update, "_sh", _pm2_stub([]))
    result = update.apply(checkout)

    assert result.ok is True
    assert result.rewrite_recovered is True
    assert result.step != "ff-check"
    new_head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert new_head == upstream_new, "must land exactly on the rewritten upstream tip"
    backed_up = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", result.backup_ref],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert backed_up == before_head, "the pre-rewrite HEAD must be preserved, not lost"


def test_history_rewrite_recovery_still_refuses_a_dirty_tree(checkout, upstream):
    """The dirty-check must still run BEFORE any rewrite recovery -- a rewrite is never
    an excuse to reset over uncommitted local edits."""
    _force_rewrite_history(upstream)
    (checkout / "README.md").write_text("dirty, uncommitted edit\n")

    result = update.apply(checkout)

    assert result.ok is False
    assert result.step == "dirty-check"


def test_ordinary_divergence_with_shared_history_still_refuses(checkout, upstream, git_calls):
    """Regression guard: a normal side-by-side divergence (both sides commit on top of the
    SAME history, not a rewrite) must keep refusing exactly as before -- only a genuine
    unrelated-history rewrite should ever trigger the reset path."""
    _commit(upstream, "new.txt", "new\n")
    _commit(checkout, "local.txt", "local\n")

    result = update.apply(checkout)

    assert result.ok is False
    assert result.step == "ff-check"
    assert result.rewrite_recovered is False
    assert not any(args and args[0] == "reset" for args in git_calls)


def _upstream_tip(checkout: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "@{u}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_is_history_rewrite_false_for_ordinary_divergence(checkout, upstream):
    # The remote-tracking tip BEFORE the fetch — an ordinary advance keeps it as an ancestor.
    old_upstream = _upstream_tip(checkout)
    _commit(upstream, "new.txt", "new\n")           # upstream advances (ff on its side)
    _commit(checkout, "local.txt", "local\n")       # and so does the local checkout
    _git(checkout, "fetch")
    assert update._is_history_rewrite(checkout, old_upstream) is False


def test_is_history_rewrite_true_after_a_force_pushed_rewrite(checkout, upstream):
    old_upstream = _upstream_tip(checkout)          # captured pre-fetch, pre-rewrite
    _force_rewrite_history(upstream)
    _git(checkout, "fetch")
    assert update._is_history_rewrite(checkout, old_upstream) is True


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


# --- auto_apply_sweep (CMX-148 part 2): the fully-UNATTENDED half, opt-in ------------

def test_auto_apply_disabled_by_default():
    assert config.AUTO_UPDATE_ENABLED is False
    assert update.auto_apply_enabled() is False


def test_auto_apply_enabled_follows_the_flag(monkeypatch):
    monkeypatch.setattr(config, "AUTO_UPDATE_ENABLED", True)
    assert update.auto_apply_enabled() is True


def _pm2_stub(restart_calls):
    def fake_sh(args, cwd, timeout=update._SHELL_TIMEOUT_SECONDS):
        if args[:2] == ["pm2", "jlist"]:
            return _FakeCP(stdout="[]")
        if args[:2] == ["uv", "sync"]:
            return _FakeCP()
        if args[0] == "pm2" and args[1] == "restart":
            restart_calls.append(args)
            return _FakeCP()
        raise AssertionError(f"unexpected _sh call: {args}")
    return fake_sh


def test_auto_apply_sweep_stays_silent_when_nothing_is_behind(checkout, monkeypatch, caplog):
    """🔴 The quiet path: with nothing behind, this must neither log nor notify — a drumbeat
    of "nothing to do" every hour is exactly the log-blindness this module warns against."""
    monkeypatch.setattr(update, "repo_root", lambda: checkout)
    stub = _StubNotify(enabled=True)
    monkeypatch.setattr(update, "notify", stub)

    with caplog.at_level(logging.WARNING, logger=update.log.name):
        result = update.auto_apply_sweep()

    assert result.ok is True
    assert result.behind_before == 0
    assert stub.sent == []
    assert caplog.records == []


def test_auto_apply_sweep_pulls_and_restarts_when_behind(checkout, upstream, monkeypatch, caplog):
    """🔴 THE LOAD-BEARING GUARD. This is the one thing part 1 refused to do: actually pull
    and restart with nobody watching. If this stops calling `apply()`, or `apply()`'s pull
    step is bypassed, this goes red without HEAD ever advancing."""
    _commit(upstream, "new.txt", "new\n")
    monkeypatch.setattr(update, "repo_root", lambda: checkout)
    restart_calls = []
    monkeypatch.setattr(update, "_sh", _pm2_stub(restart_calls))
    stub = _StubNotify(enabled=True)
    monkeypatch.setattr(update, "notify", stub)

    before_head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    with caplog.at_level(logging.WARNING, logger=update.log.name):
        result = update.auto_apply_sweep()

    after_head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    assert result.ok is True
    assert result.behind_before == 1
    assert after_head != before_head, "auto_apply_sweep must actually advance HEAD, unattended"
    assert len(stub.sent) == 1
    assert "applied" in stub.sent[0][0]
    assert any("UNATTENDED" in r.getMessage() for r in caplog.records)


def test_auto_apply_sweep_logs_and_notifies_loudly_on_refusal_without_raising(
    checkout, upstream, monkeypatch, caplog,
):
    """A dirty tree (or any other apply() refusal) must be reported loudly, not swallowed —
    unlike an auto-merge candidate, a stuck refusal here needs a human to clear it."""
    _commit(upstream, "new.txt", "new\n")
    (checkout / "README.md").write_text("dirty, uncommitted edit\n")
    monkeypatch.setattr(update, "repo_root", lambda: checkout)
    stub = _StubNotify(enabled=True)
    monkeypatch.setattr(update, "notify", stub)

    with caplog.at_level(logging.ERROR, logger=update.log.name):
        result = update.auto_apply_sweep()

    assert result.ok is False
    assert result.step == "dirty-check"
    assert len(stub.sent) == 1
    assert "FAILED" in stub.sent[0][1]
    assert any("dirty-check" in r.getMessage() for r in caplog.records)


def test_auto_apply_sweep_never_calls_apply_with_a_bespoke_repo_arg(checkout, upstream, monkeypatch):
    """Sanity: `auto_apply_sweep()` must drive the SAME `apply()` a human's `chela update`
    calls (via `repo_root()`), not some separate, unaudited code path."""
    _commit(upstream, "new.txt", "new\n")
    called = []
    real_apply = update.apply

    def spy(repo=None):
        called.append(repo)
        return real_apply(repo)

    monkeypatch.setattr(update, "apply", spy)
    monkeypatch.setattr(update, "repo_root", lambda: checkout)
    monkeypatch.setattr(update, "_sh", _pm2_stub([]))

    update.auto_apply_sweep()

    assert called == [None]  # apply() itself resolves repo_root() when not given one


# --- production call-site: the daemon loop actually switches on CHELA_AUTO_UPDATE -----

def _run_one_daemon_tick(monkeypatch) -> None:
    """Drive exactly ONE iteration of `cmd_run`'s `while not stop.stopping` loop with every
    other subsystem kept inert, so the tick reaches the update-check call-site and stops.

    Mirrors `tests/test_context.py::_run_one_daemon_tick` — the same shape of test that
    catches a call-site being unwired even though every unit test of the extracted seam
    (`auto_apply_sweep`, `check_and_notify`) stays green, because none of them exercise
    `cmd_run` itself. `last_update_check` starts at 0.0 in `cmd_run`, so the real epoch
    makes the update-check branch due on this very first pass — no need to fake time.
    """
    monkeypatch.setattr(main.GracefulShutdown, "install", lambda self: self)

    def stop_after_one(self, _seconds):
        self._event.set()
        return True

    monkeypatch.setattr(main.GracefulShutdown, "wait", stop_after_one)
    monkeypatch.setattr(main.scheduler, "init", lambda: None)
    monkeypatch.setattr(main.scheduler, "tick", lambda: 0)
    monkeypatch.setattr(main.agent_manager, "reconcile_window_names", lambda: [])
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(main.notify, "enabled", lambda: False)
    monkeypatch.setattr(main.rooms, "has_pending", lambda: False)
    monkeypatch.setattr(main.inbox, "enabled", lambda: False)
    monkeypatch.setattr(main.capabilities, "effective", lambda: [])
    monkeypatch.setattr(main.capabilities, "announce", lambda caps, log: None)
    monkeypatch.setattr(main.capabilities, "publish", lambda caps, boot_id: None)


def test_the_daemon_loop_calls_auto_apply_sweep_when_enabled(monkeypatch):
    """🔴 WIRING (production call-site) — every test above exercises `auto_apply_sweep` in
    isolation, so they ALL stay green even if `cmd_run` never reaches it (e.g. the
    `if update.auto_apply_enabled():` guard reverted to always calling `check_and_notify`).
    Drives one real tick of the daemon loop and proves the wire is connected."""
    _run_one_daemon_tick(monkeypatch)
    monkeypatch.setattr(main.update, "auto_apply_enabled", lambda: True)
    calls = []
    monkeypatch.setattr(main.update, "auto_apply_sweep", lambda: calls.append(1))
    monkeypatch.setattr(
        main.update, "check_and_notify",
        lambda behind: pytest.fail("must not call check_and_notify while auto-update is ON"),
    )

    main.cmd_run(SimpleNamespace())

    assert calls == [1], (
        "cmd_run did NOT call update.auto_apply_sweep on the loop pass even though "
        "auto_apply_enabled() is True — CHELA_AUTO_UPDATE is unwired and can be reverted "
        "with the suite green"
    )


def test_the_daemon_loop_still_only_informs_when_auto_update_is_off(monkeypatch):
    """The flip side of the wiring test above: with the flag off (the default), the loop
    must keep calling the informer, never the unattended sweep."""
    _run_one_daemon_tick(monkeypatch)
    monkeypatch.setattr(main.update, "auto_apply_enabled", lambda: False)
    monkeypatch.setattr(
        main.update, "auto_apply_sweep",
        lambda: pytest.fail("must not call auto_apply_sweep while CHELA_AUTO_UPDATE is off"),
    )
    calls = []
    monkeypatch.setattr(main.update, "check_and_notify", lambda behind: calls.append(behind) or behind)

    main.cmd_run(SimpleNamespace())

    assert calls == [0], (
        "cmd_run did NOT call update.check_and_notify on the loop pass with auto-update "
        "off — the default informer path is unwired"
    )


# --- repo_root ------------------------------------------------------------------------

def test_repo_root_refuses_a_non_git_directory(tmp_path, monkeypatch):
    plain_dir = tmp_path / "not_a_repo"
    plain_dir.mkdir()
    monkeypatch.setattr(update, "__file__", str(plain_dir / "fake_pkg" / "update.py"))
    (plain_dir / "fake_pkg").mkdir()

    with pytest.raises(update.NotAGitCheckout):
        update.repo_root()
