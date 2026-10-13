"""🏷️ CMX-87 — `chela release X.Y.Z`: the orchestrator PREPARES a release (commit + tag
LOCALLY, nothing pushed) and requests approval; the operator's approval pushes main, the tag,
then dev — fast-forward only, once. dev moving, a deny, a failed step ⇒ nothing pushed.

No live GitHub: `origin` is a local bare repository. The two `uv` steps are seams (`_uv_lock`
and `_release_tests`) faked per test; every git command is real. The request store is the
real override store under the sandboxed `CHELA_DIR` (conftest). No live tmux or Telegram.
"""
from __future__ import annotations

import re
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from chela import config, inbox, overrides, release

PYPROJECT = '[project]\nname = "demo"\nversion = "0.1.0"\n'
LOCK = ('version = 1\n\n[[package]]\nname = "click"\nversion = "8.0.0"\n\n'
        '[[package]]\nname = "demo"\nversion = "0.1.0"\nsource = { editable = "." }\n')
CHANGELOG = ("# Changelog\n\n## [Unreleased]\n\n## [0.1.0] — 2026-01-01\n\n### Added\n\n"
             "- **First.** (CMX-1)\n")


_REAL_RELEASE_TESTS = release._release_tests
_REAL_UV_LOCK = release._uv_lock
_REAL_GH_RELEASE_URL = release._gh_release_url


def _g(cwd, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _ok(*_a, **_k) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, "", "")


def _lock_to(version_of=lambda wt: None):
    """A fake `uv lock`: rewrites uv.lock's own `demo` entry to pyproject's version (or to
    ``version_of(wt)`` when that returns one)."""
    def fake(wt: Path) -> subprocess.CompletedProcess:
        v = version_of(wt) or re.search(r'(?m)^version = "([^"]+)"',
                                        (wt / "pyproject.toml").read_text()).group(1)
        lock = wt / "uv.lock"
        lock.write_text(re.sub(r'(name = "demo"\nversion = )"[^"]+"', rf'\g<1>"{v}"',
                               lock.read_text()))
        return _ok()
    return fake


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.delenv(config.ACTOR_ENV, raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv(overrides.TTL_ENV, raising=False)
    monkeypatch.setattr(overrides.notify, "enabled", lambda: False)
    monkeypatch.setattr(release.notify, "enabled", lambda: False)
    monkeypatch.setattr(release, "_uv_lock", _lock_to())
    monkeypatch.setattr(release, "_release_tests", _ok)
    monkeypatch.setattr(release, "_gh_release_url", lambda rec: None)
    release._dev_head_cache.clear()


@pytest.fixture
def told(monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(inbox, "enqueue", lambda kind, summary, payload: seen.append(
        (kind, summary, payload)) or True)
    return seen


@pytest.fixture
def pushes(monkeypatch):
    """Every `git push` chela.release ran — the real push still happens."""
    seen: list[str] = []
    real = release._git

    def spy(cwd, *args, **kw):
        if args and args[0] == "push":
            seen.append(args[-1])
        return real(cwd, *args, **kw)
    monkeypatch.setattr(release, "_git", spy)
    return seen


@pytest.fixture
def world(tmp_path):
    origin = tmp_path / "origin.git"
    _g(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _g(tmp_path, "clone", "-q", str(origin), str(work))
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"),
                 ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
        _g(work, "config", k, v)
    _g(work, "checkout", "-q", "-b", "main")
    (work / "pyproject.toml").write_text(PYPROJECT)
    (work / "uv.lock").write_text(LOCK)
    (work / "CHANGELOG.md").write_text(CHANGELOG)
    (work / "changelog.d").mkdir()
    (work / "changelog.d" / "README.md").write_text("fragments\n")
    _g(work, "add", "-A")
    _g(work, "commit", "-q", "-m", "init")
    _g(work, "push", "-q", "origin", "main")
    _g(work, "checkout", "-q", "-b", "dev")
    (work / "changelog.d" / "CMX-5.md").write_text("### Added\n\n- **Five.** (CMX-5, #10)\n")
    (work / "changelog.d" / "CMX-7.md").write_text("### Fixed\n\n- **Seven.** (CMX-7)\n")
    _g(work, "add", "-A")
    _g(work, "commit", "-q", "-m", "work")
    _g(work, "push", "-q", "origin", "dev")
    return SimpleNamespace(origin=origin, work=work, tmp=tmp_path)


def _remote(world, ref: str) -> str | None:
    p = subprocess.run(["git", "-C", str(world.origin), "rev-parse", "-q", "--verify", ref],
                       capture_output=True, text=True)
    return p.stdout.strip() or None


def _local(world, ref: str) -> str | None:
    p = subprocess.run(["git", "-C", str(world.work), "rev-parse", "-q", "--verify", ref],
                       capture_output=True, text=True)
    return p.stdout.strip() or None


def _prepare(world, version="0.2.0", theme="a calmer release") -> dict:
    return release.prepare(version, theme, body="Longer notes.", repo=world.work)


def _release_worktrees() -> list[Path]:
    d = config.CHELA_DIR / "releases"
    return list(d.iterdir()) if d.is_dir() else []


# --- the request ---------------------------------------------------------------------------

def test_request_commits_and_tags_locally_and_pushes_nothing(world, pushes):
    """🔴 GUARD: the request half commits + tags LOCALLY and pushes NOTHING — one pending
    request, bound to the dev commit, carrying the version and the CHANGELOG's task ids."""
    main0, dev0 = _remote(world, "main"), _remote(world, "dev")
    out = _prepare(world)
    assert out["ok"] is True and out["pending"] is True, out
    assert pushes == []
    assert _remote(world, "main") == main0 and _remote(world, "dev") == dev0
    assert _remote(world, "refs/tags/v0.2.0") is None
    sha = out["release_sha"]
    assert _local(world, "refs/tags/v0.2.0^{commit}") == sha
    assert _local(world, f"{sha}^") == dev0 == out["dev_sha"]
    assert _g(world.work, "cat-file", "-p", "v0.2.0").endswith("0.2.0 — a calmer release")
    msg = _g(world.work, "log", "-1", "--format=%B", sha)
    assert msg.startswith("Release 0.2.0 — a calmer release") and "Longer notes." in msg
    tree = _g(world.work, "ls-tree", "-r", "--name-only", sha).split()
    assert "changelog.d/CMX-5.md" not in tree and "changelog.d/README.md" in tree
    changelog = _g(world.work, "show", f"{sha}:CHANGELOG.md")
    assert "## [0.2.0] — " in changelog and "## [Unreleased]" in changelog
    assert 'version = "0.2.0"' in _g(world.work, "show", f"{sha}:pyproject.toml")
    assert 'name = "demo"\nversion = "0.2.0"' in _g(world.work, "show", f"{sha}:uv.lock")

    pending, = overrides.pending()
    assert pending["id"] == out["request_id"] and pending["kind"] == "release"
    assert pending["head_sha"] == dev0 and pending["task_ids"] == ["CMX-5", "CMX-7"]
    from chela.telegram.overridewatch import format_release_message
    card = format_release_message(pending)
    assert "v0.2.0" in card and "0.2.0" in card and "a calmer release" in card
    assert "CMX-5, CMX-7" in card and dev0[:12] in card


def test_the_bridge_posts_one_card_with_the_version_and_tasks_and_two_buttons(world):
    from chela.telegram.interactive import decode_override_callback
    from chela.telegram.overridewatch import OverrideRelay
    rid = _prepare(world)["request_id"]
    posts, edits = [], []
    relay = OverrideRelay(post=lambda text, **kw: posts.append((text, kw)) or 7,
                          edit=lambda mid, text, **kw: edits.append((mid, text)) or True)
    relay.tick()
    relay.tick()
    (text, kw), = posts
    assert "Release v0.2.0" in text and "CMX-5, CMX-7" in text and edits == []
    buttons = [b for row in kw["reply_markup"]["inline_keyboard"] for b in row]
    assert [decode_override_callback(b["callback_data"]) for b in buttons] == [
        (True, rid), (False, rid)]


def test_a_second_release_while_one_is_pending_is_refused(world):
    assert _prepare(world)["ok"]
    out = _prepare(world, version="0.3.0")
    assert out["ok"] is False and "already waiting" in out["error"]
    assert len(overrides.pending()) == 1


# --- approve -------------------------------------------------------------------------------

def test_approve_pushes_main_tag_dev_in_order_exactly_once(world, pushes, told):
    """🔴 GUARD: approve ⇒ exactly three pushes, main → tag → dev, none forced; a second
    approve pushes nothing more."""
    out = _prepare(world)
    rid, sha = out["request_id"], out["release_sha"]
    first = overrides.decide(rid, True, "terminal:op")
    assert first["ok"] is True and first["state"] == overrides.RELEASED, first
    second = overrides.decide(rid, True, "terminal:op")
    assert second["ok"] is False and second["state"] == overrides.RELEASED
    assert pushes == [f"{sha}:refs/heads/main", "refs/tags/v0.2.0:refs/tags/v0.2.0",
                      f"{sha}:refs/heads/dev"]
    assert not any(p.startswith("+") for p in pushes)
    assert _remote(world, "main") == _remote(world, "dev") == sha
    assert _remote(world, "refs/tags/v0.2.0^{commit}") == sha
    assert overrides.load(rid)["state"] == overrides.RELEASED
    assert told == []
    # its worktree and local branch are gone; the pushed tag stays
    assert _release_worktrees() == []
    assert _local(world, "refs/heads/chela-release/v0.2.0") is None
    assert _local(world, "refs/tags/v0.2.0") is not None


def test_the_telegram_tap_releases_and_the_card_ends_on_the_release_url(world, monkeypatch):
    from chela.telegram.overridewatch import OverrideRelay, handle_override_tap
    out = _prepare(world)
    posts, edits = [], []
    relay = OverrideRelay(post=lambda text, **kw: posts.append(kw) or 7,
                          edit=lambda mid, text, **kw: edits.append((text, kw)) or True)
    relay.tick()
    approve = posts[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert handle_override_tap(approve).startswith("✅ Released")
    relay.tick()
    (text, kw), = edits
    assert f"released {out['release_sha'][:12]}" in text and kw["reply_markup"] is None
    url = "https://github.com/o/r/releases/tag/v0.2.0"
    monkeypatch.setattr(release, "_gh_release_url", lambda rec: url)
    monkeypatch.setattr(release, "RELEASE_URL_EVERY_S", 0.0)     # skip the per-minute throttle
    relay.tick()
    relay.tick()
    assert len(edits) == 2 and f"Release: {url}" in edits[1][0]


def test_the_dashboard_confirm_page_shows_the_release_and_approves(world, pushes):
    pytest.importorskip("flask")
    from chela.dashboard.app import app
    out = _prepare(world)
    client = app.test_client()
    page = client.get(f"/override/{out['request_id']}")
    assert page.status_code == 200 and b"Approve release" in page.data
    assert b"CMX-5, CMX-7" in page.data and out["dev_sha"].encode() in page.data
    ok = client.post(f"/override/{out['request_id']}", data={"decision": "approve"})
    assert ok.status_code == 200 and b"released" in ok.data
    assert len(pushes) == 3


# --- dev moved -----------------------------------------------------------------------------

def _move_dev(world) -> str:
    other = world.tmp / "other"
    _g(world.tmp, "clone", "-q", "-b", "dev", str(world.origin), str(other))
    _g(other, "-c", "user.email=o@example.com", "-c", "user.name=O", "-c",
       "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "late merge")
    _g(other, "push", "-q", "origin", "dev")
    return _g(other, "rev-parse", "HEAD")


def test_approve_after_dev_moved_pushes_nothing_expires_and_tells(world, pushes, told):
    """🔴 GUARD: dev moved after the request ⇒ the approval pushes NOTHING, the request is
    expired, the operator is told — and the local tag/branch are cleaned up."""
    out = _prepare(world)
    main0 = _remote(world, "main")
    moved = _move_dev(world)
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["ok"] is False and res["state"] == overrides.EXPIRED, res
    assert pushes == []
    assert _remote(world, "main") == main0 and _remote(world, "dev") == moved
    assert _remote(world, "refs/tags/v0.2.0") is None
    assert overrides.load(out["request_id"])["state"] == overrides.EXPIRED
    assert [k for k, *_ in told] == ["release_expired"] and "nothing was pushed" in told[0][1]
    assert _local(world, "refs/tags/v0.2.0") is None and _release_worktrees() == []


def test_the_sweep_expires_a_release_when_dev_moves(world, pushes, told):
    """🔴 GUARD: a push to dev after the request expires it — no approval needed."""
    out = _prepare(world)
    fresh = lambda rec: release.remote_dev_head(rec, max_age=0)  # noqa: E731
    assert overrides.sweep(dev_head_of=fresh) == []          # dev unchanged ⇒ kept
    _move_dev(world)
    expired, = overrides.sweep(dev_head_of=fresh)
    assert expired["id"] == out["request_id"] and expired["state"] == overrides.EXPIRED
    assert "dev moved" in expired["detail"]
    assert overrides.decide(out["request_id"], True, "terminal:op")["ok"] is False
    assert pushes == [] and [k for k, *_ in told] == ["release_expired"]


def test_main_not_an_ancestor_of_the_release_at_approval_pushes_nothing(world, pushes, told):
    out = _prepare(world)
    other = world.tmp / "hotfix"
    _g(world.tmp, "clone", "-q", "-b", "main", str(world.origin), str(other))
    _g(other, "-c", "user.email=o@example.com", "-c", "user.name=O", "-c",
       "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "hotfix on main")
    _g(other, "push", "-q", "origin", "main")
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["state"] == overrides.FAILED and "ancestor" in res["detail"]
    assert pushes == [] and [k for k, *_ in told] == ["release_failed"]


# --- a failed step refuses -----------------------------------------------------------------

def _assert_refused_and_clean(world, out, *, why: str):
    assert out["ok"] is False and why in out["error"], out
    assert overrides.all_requests() == []
    assert _local(world, "refs/tags/v0.2.0") is None
    assert _local(world, "refs/heads/chela-release/v0.2.0") is None
    assert _release_worktrees() == []


def test_red_release_tests_refuse_with_no_request(world, pushes, monkeypatch):
    """🔴 GUARD: the release tests failing ⇒ refused, no request, nothing left behind."""
    monkeypatch.setattr(release, "_release_tests", lambda wt: subprocess.CompletedProcess(
        [], 1, "1 failed: test_version", ""))
    _assert_refused_and_clean(world, _prepare(world), why="release tests failed")
    assert pushes == []


def test_a_uv_lock_that_changes_nothing_refuses(world, monkeypatch):
    """🔴 GUARD: `uv lock` leaving uv.lock untouched (still the previous version) refuses."""
    monkeypatch.setattr(release, "_uv_lock", _ok)
    _assert_refused_and_clean(world, _prepare(world), why="changed nothing")


def test_a_lock_naming_another_version_refuses(world, monkeypatch):
    """🔴 GUARD: uv.lock must record the NEW version — a lock naming any other refuses."""
    monkeypatch.setattr(release, "_uv_lock", _lock_to(lambda wt: "0.1.5"))
    _assert_refused_and_clean(world, _prepare(world), why="version mismatch")


def test_a_failed_uv_lock_refuses(world, monkeypatch):
    monkeypatch.setattr(release, "_uv_lock", lambda wt: subprocess.CompletedProcess(
        [], 2, "", "resolution failed"))
    _assert_refused_and_clean(world, _prepare(world), why="`uv lock` failed")


def test_a_step_that_leaves_a_stray_file_refuses_as_dirty(world, monkeypatch):
    def dirty(wt):
        (wt / "stray.txt").write_text("x")
        return _ok()
    monkeypatch.setattr(release, "_release_tests", dirty)
    _assert_refused_and_clean(world, _prepare(world), why="dirty")


@pytest.mark.parametrize("version,why", [("0.1.0", "not newer"), ("0.0.9", "not newer"),
                                         ("1.2", "not a release version")])
def test_a_version_that_is_not_newer_refuses(world, version, why):
    out = release.prepare(version, "t", repo=world.work)
    assert out["ok"] is False and why in out["error"]
    assert overrides.all_requests() == [] and _release_worktrees() == []


def test_main_not_an_ancestor_of_dev_refuses(world):
    other = world.tmp / "hotfix"
    _g(world.tmp, "clone", "-q", "-b", "main", str(world.origin), str(other))
    _g(other, "-c", "user.email=o@example.com", "-c", "user.name=O", "-c",
       "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "hotfix on main")
    _g(other, "push", "-q", "origin", "main")
    out = _prepare(world)
    assert out["ok"] is False and "not an ancestor" in out["error"]
    assert overrides.all_requests() == [] and _release_worktrees() == []


def test_a_release_with_no_entries_refuses(world):
    _g(world.work, "rm", "-q", "changelog.d/CMX-5.md", "changelog.d/CMX-7.md")
    _g(world.work, "commit", "-q", "-m", "drop")
    _g(world.work, "push", "-q", "origin", "dev")
    _assert_refused_and_clean(world, _prepare(world), why="nothing to release")


# --- deny / human-only ---------------------------------------------------------------------

def test_deny_pushes_nothing_and_cleans_up_the_local_tag_and_branch(world, pushes):
    """🔴 GUARD: deny ⇒ nothing pushed; the local tag, branch and worktree are removed."""
    out = _prepare(world)
    assert _local(world, "refs/tags/v0.2.0") and _release_worktrees()
    res = overrides.decide(out["request_id"], False, "terminal:op")
    assert res["ok"] is True and res["state"] == overrides.DENIED
    assert pushes == []
    assert _remote(world, "refs/tags/v0.2.0") is None
    assert _local(world, "refs/tags/v0.2.0") is None
    assert _local(world, "refs/heads/chela-release/v0.2.0") is None
    assert _release_worktrees() == []


@pytest.mark.parametrize("env", [{config.ACTOR_ENV: config.AUTO_ORCHESTRATOR_ACTOR},
                                 {"CLAUDECODE": "1"}])
def test_an_agent_session_cannot_approve_a_release(world, pushes, env):
    """🔴 GUARD: the orchestrator may REQUEST a release, never approve one."""
    out = _prepare(world)
    res = overrides.decide(out["request_id"], True, "terminal:op", env=env)
    assert res["ok"] is False and res.get("refused")
    assert pushes == [] and overrides.load(out["request_id"])["state"] == overrides.PENDING


def test_merge_approve_cli_refuses_a_release_inside_an_agent_session(world, pushes,
                                                                     monkeypatch, capsys):
    from chela import main
    out = _prepare(world)
    monkeypatch.setenv(config.ACTOR_ENV, config.AUTO_ORCHESTRATOR_ACTOR)
    with pytest.raises(SystemExit) as exc:
        main.cmd_merge_approve(SimpleNamespace(request=out["request_id"], deny=False))
    assert exc.value.code == 1 and pushes == []
    assert "never approve" in capsys.readouterr().out


def test_complete_refuses_a_request_that_was_never_approved(world, pushes):
    """🔴 GUARD: `release.complete` pushes only for a request whose STORED state says it was
    approved — calling it on a pending request (skipping `decide`) pushes nothing."""
    out = _prepare(world)
    res = release.complete(overrides.load(out["request_id"]), "me")
    assert res["ok"] is False and "not approved" in res["detail"]
    assert pushes == []


# --- the CLI -------------------------------------------------------------------------------

def test_the_release_cli_prints_the_pending_request(world, capsys, tmp_path):
    from chela import main
    body = tmp_path / "body.md"
    body.write_text("Body text.\n")
    main.cmd_release(SimpleNamespace(version="0.2.0", theme="a theme", body_file=str(body),
                                     repo=str(world.work)))
    printed = capsys.readouterr().out
    rid = overrides.pending()[0]["id"]
    assert "PENDING" in printed and rid in printed and "NOTHING pushed" in printed
    assert "CMX-5, CMX-7" in printed


def test_the_release_cli_exits_nonzero_on_a_refusal(world, capsys, monkeypatch):
    from chela import main
    monkeypatch.setattr(release, "_uv_lock", _ok)
    with pytest.raises(SystemExit) as exc:
        main.cmd_release(SimpleNamespace(version="0.2.0", theme="t", body_file=None,
                                         repo=str(world.work)))
    assert exc.value.code == 1 and "REFUSED" in capsys.readouterr().out


# --- every pre-push re-check refuses on its own (DEFEAT_SHAPES 12b / 87) -------------------
# Each test arms exactly ONE precondition with a real violation (every other check still
# passes) and asserts the ABSENCE of the push — origin untouched — not just an error text.
# The negative control is test_approve_pushes_main_tag_dev_in_order_exactly_once: same world,
# no violation ⇒ three pushes.

def _push_tag_to_origin(world, target: str = "main") -> str:
    """A v0.2.0 tag lands on origin from another clone — on a commit outside main/dev's
    history, so `git fetch`'s tag auto-follow never brings it home: only origin has it."""
    other = world.tmp / "tagger"
    _g(world.tmp, "clone", "-q", "-b", target, str(world.origin), str(other))
    ident = ("-c", "user.email=o@example.com", "-c", "user.name=O")
    side = _g(other, *ident, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "side")
    _g(other, *ident, "-c", "tag.gpgsign=false",
       "tag", "-a", "v0.2.0", "-m", "someone else's v0.2.0", side)
    _g(other, "push", "-q", "origin", "refs/tags/v0.2.0")
    return _remote(world, "refs/tags/v0.2.0^{commit}")


def _assert_origin_untouched(world, main0, dev0, pushes, tag=None):
    assert pushes == []
    assert _remote(world, "main") == main0 and _remote(world, "dev") == dev0
    assert _remote(world, "refs/tags/v0.2.0^{commit}") == tag


def test_approve_with_the_tag_already_on_origin_pushes_nothing(world, pushes, told):
    """🔴 GUARD: a v0.2.0 tag that reached origin after the request ⇒ the approval pushes
    NOTHING (main and dev stay put), the request fails, the operator is told."""
    out = _prepare(world)
    main0, dev0 = _remote(world, "main"), _remote(world, "dev")
    theirs = _push_tag_to_origin(world)
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["ok"] is False and res["state"] == overrides.FAILED, res
    assert "already exists on origin" in res["detail"]
    _assert_origin_untouched(world, main0, dev0, pushes, tag=theirs)
    assert overrides.load(out["request_id"])["state"] == overrides.FAILED
    assert [k for k, *_ in told] == ["release_failed"] and "nothing was pushed" in told[0][1]


def test_approve_when_origin_tags_cannot_be_read_pushes_nothing(world, pushes, monkeypatch):
    """🔴 GUARD: origin's tags unreadable at approval (ls-remote fails) ⇒ fail CLOSED."""
    out = _prepare(world)
    main0, dev0 = _remote(world, "main"), _remote(world, "dev")
    monkeypatch.setattr(release, "_remote_tag", lambda cwd, tag: None)
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["state"] == overrides.FAILED and "could not be checked" in res["detail"]
    _assert_origin_untouched(world, main0, dev0, pushes)


def test_approve_when_the_local_tag_was_moved_pushes_nothing(world, pushes):
    """🔴 GUARD: the local tag no longer points at the approved commit ⇒ nothing pushed —
    and cleanup leaves that (no longer ours) tag alone."""
    out = _prepare(world)
    main0, dev0 = _remote(world, "main"), _remote(world, "dev")
    _g(world.work, "-c", "tag.gpgsign=false", "tag", "-f", "-a", "v0.2.0", "-m", "moved",
       dev0)
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["state"] == overrides.FAILED and "no longer points" in res["detail"], res
    _assert_origin_untouched(world, main0, dev0, pushes)
    assert _local(world, "refs/tags/v0.2.0^{commit}") == dev0     # not deleted: not ours


def test_approve_when_the_release_is_not_one_commit_on_dev_pushes_nothing(world, pushes):
    """🔴 GUARD: the approved commit must be ONE commit on top of the dev it was cut from.
    A record whose release commit sits two commits above dev (tag moved with it, so the
    tag and main-ancestor checks both still pass) ⇒ nothing pushed."""
    out = _prepare(world)
    main0, dev0 = _remote(world, "main"), _remote(world, "dev")
    sha = out["release_sha"]
    extra = _g(world.work, "-c", "user.email=o@example.com", "-c", "user.name=O",
               "commit-tree", f"{sha}^{{tree}}", "-p", sha, "-m", "smuggled")
    _g(world.work, "-c", "tag.gpgsign=false", "tag", "-f", "-a", "v0.2.0", "-m", "x", extra)
    overrides.set_fields(out["request_id"], release_sha=extra)
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["state"] == overrides.FAILED and "single commit" in res["detail"], res
    _assert_origin_untouched(world, main0, dev0, pushes)


def test_approve_when_the_repository_is_gone_pushes_nothing(world, pushes):
    out = _prepare(world)
    overrides.set_fields(out["request_id"], git_dir=str(world.tmp / "nowhere"))
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["state"] == overrides.FAILED and "gone" in res["detail"]
    assert pushes == []


def test_approve_when_origin_cannot_be_fetched_pushes_nothing(world, pushes):
    out = _prepare(world)
    _g(world.work, "remote", "set-url", "origin", str(world.tmp / "missing.git"))
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["state"] == overrides.FAILED and "fetch" in res["detail"]
    assert pushes == []


def test_complete_refuses_an_approved_override_that_is_not_a_release(world, pushes):
    """🔴 GUARD: `release.complete` pushes only for a stored KIND=release record."""
    out = _prepare(world)
    overrides.set_fields(out["request_id"], state=overrides.MERGING, kind="override")
    res = release.complete(overrides.load(out["request_id"]), "me")
    assert res["ok"] is False and "not approved" in res["detail"] and pushes == []
    overrides.set_fields(out["request_id"], kind="release")     # negative control
    assert release.complete(overrides.load(out["request_id"]), "me")["ok"] is True
    assert len(pushes) == 3


def test_a_rejected_dev_push_fails_and_reports_what_was_pushed(world, pushes, told):
    """🔴 GUARD: a push failing midway stops there, the request FAILS (not released) and
    says exactly what reached origin; the pushed tag is kept."""
    out = _prepare(world)
    dev0 = _remote(world, "dev")
    hook = world.origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nwhile read o n r; do [ \"$r\" = refs/heads/dev ] && "
                    "exit 1; done\nexit 0\n")
    hook.chmod(0o755)
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["ok"] is False and res["state"] == overrides.FAILED, res
    assert "pushing dev failed" in res["detail"] and "main, v0.2.0" in res["detail"]
    assert len(pushes) == 3 and _remote(world, "dev") == dev0
    assert overrides.load(out["request_id"])["pushed"] == ["main", "v0.2.0"]
    assert "pushed only: main, v0.2.0" in told[0][1]
    assert _local(world, "refs/tags/v0.2.0") is not None


# --- prepare's own preconditions ----------------------------------------------------------

def test_prepare_refuses_when_the_tag_already_exists_on_origin(world, pushes):
    """🔴 GUARD: a v0.2.0 tag already on origin (not fetched locally) ⇒ refused, no request,
    no worktree/branch/local tag."""
    theirs = _push_tag_to_origin(world)
    assert _local(world, "refs/tags/v0.2.0") is None             # only origin has it
    out = _prepare(world)
    assert out["ok"] is False and "already exists on origin" in out["error"], out
    assert overrides.all_requests() == [] and _release_worktrees() == [] and pushes == []
    assert _local(world, "refs/heads/chela-release/v0.2.0") is None
    assert _remote(world, "refs/tags/v0.2.0^{commit}") == theirs


def test_prepare_refuses_when_origin_tags_cannot_be_read(world, monkeypatch):
    monkeypatch.setattr(release, "_remote_tag", lambda cwd, tag: None)
    out = _prepare(world)
    assert out["ok"] is False and "could not read origin's tags" in out["error"]
    assert overrides.all_requests() == [] and _release_worktrees() == []


def test_prepare_refuses_when_the_tag_exists_locally_and_keeps_it(world):
    _g(world.work, "-c", "tag.gpgsign=false", "tag", "-a", "v0.2.0", "-m", "mine", "main")
    mine = _local(world, "refs/tags/v0.2.0^{commit}")
    out = _prepare(world)
    assert out["ok"] is False and "already exists locally" in out["error"]
    assert overrides.all_requests() == [] and _release_worktrees() == []
    assert _local(world, "refs/tags/v0.2.0^{commit}") == mine


def test_prepare_refuses_when_the_release_branch_exists_and_keeps_it(world):
    """The named refusal — not `git worktree add -b`'s own failure further down — so the
    operator is told how to clear it."""
    _g(world.work, "branch", "chela-release/v0.2.0", "main")
    out = _prepare(world)
    assert out["ok"] is False and "a previous release attempt" in out["error"], out
    assert "git branch -D chela-release/v0.2.0" in out["error"]
    assert overrides.all_requests() == [] and _release_worktrees() == []
    assert _local(world, "refs/heads/chela-release/v0.2.0") == _remote(world, "main")
    assert _local(world, "refs/tags/v0.2.0") is None


def test_prepare_refuses_when_origin_cannot_be_fetched(world):
    _g(world.work, "remote", "set-url", "origin", str(world.tmp / "missing.git"))
    out = _prepare(world)
    assert out["ok"] is False and "fetch" in out["error"]
    assert overrides.all_requests() == [] and _release_worktrees() == []


# --- orchestrator-added guards (judge round 2 survivors) ----------------------------------

def test_a_rejected_main_push_stops_before_the_tag_and_dev(world, pushes, told):
    """🔴 GUARD: a push that fails stops the sequence — a refused MAIN push must never be
    followed by the tag or dev push."""
    out = _prepare(world)
    main0, dev0 = _remote(world, "main"), _remote(world, "dev")
    hook = world.origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nwhile read o n r; do [ \"$r\" = refs/heads/main ] && "
                    "exit 1; done\nexit 0\n")
    hook.chmod(0o755)
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["ok"] is False and res["state"] == overrides.FAILED, res
    assert "pushing main failed" in res["detail"]
    assert len(pushes) == 1, f"no push may follow a refused main push: {pushes}"
    assert _remote(world, "main") == main0 and _remote(world, "dev") == dev0
    assert _remote(world, "refs/tags/v0.2.0") is None
    assert overrides.load(out["request_id"])["pushed"] == []


def test_unreadable_origin_tags_read_as_unknown_not_absent(world):
    """🔴 GUARD: the real `_remote_tag` returns None (UNKNOWN) when `ls-remote` fails — never
    False ("tag absent"), which would let a release push over a tag it could not see."""
    assert release._remote_tag(world.work, "v0.2.0") is False, "control: readable and absent"
    _g(world.work, "remote", "set-url", "origin", str(world.tmp / "no-such-origin.git"))
    assert release._remote_tag(world.work, "v0.2.0") is None


@pytest.mark.parametrize("exc", [FileNotFoundError("uv"),
                                 subprocess.TimeoutExpired(["uv"], 900)])
def test_release_tests_that_cannot_run_count_as_failed(monkeypatch, tmp_path, exc):
    """🔴 GUARD: a release-tests run that cannot complete (uv missing / timeout) is a FAILED
    step, never a pass."""
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(release.subprocess, "run", boom)
    p = _REAL_RELEASE_TESTS(tmp_path)
    assert p.returncode != 0


@pytest.mark.parametrize("exc", [FileNotFoundError("uv"),
                                 subprocess.TimeoutExpired(["uv"], 300)])
def test_a_uv_lock_that_cannot_run_counts_as_failed(monkeypatch, tmp_path, exc):
    """🔴 GUARD: same rule for `uv lock`: an exception is a FAILED step, never a pass."""
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(release.subprocess, "run", boom)
    assert _REAL_UV_LOCK(tmp_path).returncode != 0


# --- orchestrator-added guards (judge round 3 survivors) ----------------------------------

def test_complete_acts_only_on_the_stored_state_never_the_callers_record(world, pushes):
    """🔴 GUARD: `complete()` re-reads the request from disk. An in-memory record that
    CLAIMS 'merging' while the stored request is still pending must push nothing."""
    out = _prepare(world)
    main0, dev0 = _remote(world, "main"), _remote(world, "dev")
    claimed = {**overrides.load(out["request_id"]), "state": overrides.MERGING}
    res = release.complete(claimed, "terminal:op")
    assert res["ok"] is False and res["pushed"] == []
    assert pushes == []
    assert _remote(world, "main") == main0 and _remote(world, "dev") == dev0
    assert overrides.load(out["request_id"])["state"] == overrides.PENDING


def test_a_step_that_modifies_a_tracked_file_outside_the_release_paths_refuses(world,
                                                                             monkeypatch):
    """🔴 GUARD: anything changed outside the release paths makes the step DIRTY — a
    modified TRACKED file too, not only an untracked one."""
    (world.work / "README.md").write_text("hello\n")
    _g(world.work, "add", "README.md")
    _g(world.work, "commit", "-q", "-m", "readme")
    _g(world.work, "push", "-q", "origin", "dev")

    def touch(wt):
        (wt / "README.md").write_text("changed by a release step\n")
        return _ok()
    monkeypatch.setattr(release, "_release_tests", touch)
    _assert_refused_and_clean(world, _prepare(world), why="dirty")


@pytest.mark.parametrize("exc", [FileNotFoundError("git"),
                                 subprocess.TimeoutExpired(["git"], 120)])
def test_a_git_command_that_cannot_complete_reads_as_failed(monkeypatch, tmp_path, exc):
    """🔴 GUARD: `_git` never raises, and a git call that could not complete (missing git,
    timeout) is a FAILURE — a timed-out push must never be counted as pushed."""
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(release.subprocess, "run", boom)
    assert release._git(tmp_path, "push", "origin", "x").returncode != 0


@pytest.mark.parametrize("rc,url", [(1, "https://github.com/o/r/releases/tag/v0.2.0"),
                                    (0, "not a url"), (0, "http://insecure.example/x"),
                                    (0, "")])
def test_the_release_url_is_linked_only_on_success_with_an_https_url(monkeypatch, rc, url):
    """🔴 GUARD: the card links a Release URL only when `gh release view` succeeded AND
    returned an https URL."""
    monkeypatch.setattr(release.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, rc, url + "\n", ""))
    assert _REAL_GH_RELEASE_URL({"repo_slug": "o/r", "tag": "v0.2.0"}) is None


def test_the_release_url_is_linked_on_success(monkeypatch):
    """Control for the test above: success + https ⇒ the URL."""
    url = "https://github.com/o/r/releases/tag/v0.2.0"
    monkeypatch.setattr(release.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, url + "\n", ""))
    assert _REAL_GH_RELEASE_URL({"repo_slug": "o/r", "tag": "v0.2.0"}) == url


# --- orchestrator-added: every remaining check in chela/release.py (self-audit) -----------

def test_a_release_with_no_theme_refuses(world):
    out = release.prepare("0.2.0", "   ", repo=world.work)
    assert out["ok"] is False and "--theme" in out["error"]
    assert overrides.all_requests() == []


def test_prepare_outside_a_git_repository_refuses(tmp_path):
    out = release.prepare("0.2.0", "a theme", repo=tmp_path)
    assert out["ok"] is False and "not inside a git repository" in out["error"]


def test_prepare_refuses_when_origin_has_no_dev_branch(world, monkeypatch):
    # (a real fetch of a missing dev already fails earlier; this pins the next check)
    monkeypatch.setattr(release, "_fetch", lambda cwd: None)
    _g(world.work, "update-ref", "-d", "refs/remotes/origin/dev")
    out = _prepare(world)
    assert out["ok"] is False and "no `main` and `dev`" in out["error"]
    assert overrides.all_requests() == []


def test_a_pyproject_with_two_version_lines_refuses(world):
    p = world.work / "pyproject.toml"
    p.write_text(p.read_text() + '\n[tool.other]\nname = "x"\nversion = "9.9.9"\n')
    _g(world.work, "commit", "-qam", "second version line")
    _g(world.work, "push", "-q", "origin", "dev")
    _assert_refused_and_clean(world, _prepare(world), why="exactly one")


def _fail_git(monkeypatch, *match: str):
    """`_git` fails (rc 1) for a call whose leading args equal ``match``; real otherwise."""
    real = release._git

    def fake(cwd, *args, **kw):
        if args[:len(match)] == match:
            return subprocess.CompletedProcess(["git", *args], 1, "", "boom")
        return real(cwd, *args, **kw)
    monkeypatch.setattr(release, "_git", fake)


def test_a_failed_worktree_add_refuses(world, monkeypatch):
    _fail_git(monkeypatch, "worktree", "add")
    out = _prepare(world)
    assert out["ok"] is False and "`git worktree add` failed" in out["error"]
    assert overrides.all_requests() == []


@pytest.mark.parametrize("step,why", [(("add", "-A"), "`git add` failed"),
                                      (("status", "--porcelain"), "worktree is dirty"),
                                      (("commit",), "`git commit` failed"),
                                      (("tag", "-a"), "`git tag` failed")])
def test_a_failed_git_step_refuses_and_leaves_nothing(world, monkeypatch, step, why):
    _fail_git(monkeypatch, *step)
    out = _prepare(world)
    assert out["ok"] is False and why in out["error"], out
    assert overrides.all_requests() == [] and _release_worktrees() == []
    assert _local(world, "refs/tags/v0.2.0") is None
    assert _local(world, "refs/heads/chela-release/v0.2.0") is None


def _track_other_py(world):
    (world.work / "other.py").write_text("x = 1\n" * 20)
    _g(world.work, "add", "other.py")
    _g(world.work, "commit", "-q", "-m", "other")
    _g(world.work, "push", "-q", "origin", "dev")


def _rename_step(monkeypatch, src: str, dst: str):
    def mv(wt):
        _g(wt, "mv", src, dst)
        return _ok()
    monkeypatch.setattr(release, "_release_tests", mv)


def test_a_rename_from_outside_into_the_release_paths_refuses_naming_it(world, monkeypatch):
    """🔴 GUARD: `R  other.py -> changelog.d/x.md` has ONE end outside RELEASE_PATHS — it
    would ship other.py's deletion. Every end must be a release path (all, not any)."""
    _track_other_py(world)
    _rename_step(monkeypatch, "other.py", "changelog.d/x.md")
    out = _prepare(world)
    _assert_refused_and_clean(world, out, why="dirty")
    assert "other.py" in out["error"]


def test_a_rename_from_the_release_paths_to_outside_refuses_naming_it(world, monkeypatch):
    _rename_step(monkeypatch, "changelog.d/README.md", "other.py")
    out = _prepare(world)
    _assert_refused_and_clean(world, out, why="dirty")
    assert "other.py" in out["error"]


def test_a_rename_inside_the_release_paths_is_allowed(world, monkeypatch):
    _rename_step(monkeypatch, "changelog.d/README.md", "changelog.d/NOTES.md")
    out = _prepare(world)
    assert out["ok"] is True, out


def test_a_quoted_outside_path_refuses(world, monkeypatch):
    """git prints a name with spaces QUOTED; a quoted path must still read as outside."""
    def stray(wt):
        (wt / "stray file.txt").write_text("x")
        return _ok()
    monkeypatch.setattr(release, "_release_tests", stray)
    _assert_refused_and_clean(world, _prepare(world), why="dirty")


def test_a_quoted_rename_into_the_release_paths_refuses(world, monkeypatch):
    (world.work / "my other.py").write_text("x = 1\n" * 20)
    _g(world.work, "add", "my other.py")
    _g(world.work, "commit", "-q", "-m", "spaced")
    _g(world.work, "push", "-q", "origin", "dev")
    _rename_step(monkeypatch, "my other.py", "changelog.d/x.md")
    _assert_refused_and_clean(world, _prepare(world), why="dirty")


@pytest.mark.xfail(strict=True, reason="PRODUCTION FINDING: porcelain QUOTES a name with "
                   "spaces, so `_in_release_paths('\"changelog.d/a b.md\"')` is False — a "
                   "legitimate in-release file is refused (fail-closed, never a slip-through)")
def test_a_quoted_path_inside_the_release_paths_is_allowed(world, monkeypatch):
    def spaced(wt):
        (wt / "changelog.d" / "a b.md").write_text("x")
        return _ok()
    monkeypatch.setattr(release, "_release_tests", spaced)
    assert _prepare(world)["ok"] is True


def test_an_unrecordable_request_refuses_and_leaves_nothing(world, monkeypatch):
    monkeypatch.setattr(release.overrides, "create", lambda meta, **k: None)
    out = _prepare(world)
    assert out["ok"] is False and "could not be recorded" in out["error"]
    assert _release_worktrees() == []
    assert _local(world, "refs/tags/v0.2.0") is None
    assert _local(world, "refs/heads/chela-release/v0.2.0") is None


def test_the_request_notifies_the_operator_when_notify_is_on(world, monkeypatch):
    sent = []
    monkeypatch.setattr(release.notify, "enabled", lambda: True)
    monkeypatch.setattr(release.notify, "send", lambda text, title=None, **k: sent.append(title))
    assert _prepare(world)["ok"] is True
    assert sent == ["chela: approve release v0.2.0?"]


def test_no_release_url_lookup_without_a_repo_slug(monkeypatch):
    monkeypatch.setattr(release.subprocess, "run",
                        lambda *a, **k: pytest.fail("gh must not run without a repo slug"))
    assert _REAL_GH_RELEASE_URL({"tag": "v0.2.0"}) is None


def test_remote_dev_head_is_none_without_a_repository():
    assert release.remote_dev_head({"git_dir": "/no/such/repo"}) is None
    assert release.remote_dev_head({}) is None


def test_remote_dev_head_is_cached_per_repository(world, monkeypatch):
    calls = []
    real = release._git

    def spy(cwd, *args, **kw):
        if args[:1] == ("ls-remote",):
            calls.append(args)
        return real(cwd, *args, **kw)
    monkeypatch.setattr(release, "_git", spy)
    rec = {"git_dir": str(world.work)}
    first = release.remote_dev_head(rec, max_age=3600)
    assert first == _remote(world, "dev")
    assert release.remote_dev_head(rec, max_age=3600) == first and len(calls) == 1
    release.remote_dev_head(rec, max_age=0)
    assert len(calls) == 2, "an expired cache entry is re-read"


def _released(world) -> str:
    out = _prepare(world)
    assert overrides.decide(out["request_id"], True, "terminal:op")["state"] == overrides.RELEASED
    return out["request_id"]


def test_poll_release_urls_records_the_url_once_and_throttles(world, monkeypatch):
    rid = _released(world)
    seen = []
    answer = {"url": None}
    monkeypatch.setattr(release, "_gh_release_url", lambda rec: seen.append(1) or answer["url"])
    t = overrides.load(rid).get("finished_at") or 1_000_000.0
    release.poll_release_urls(now=t + 1)
    assert len(seen) == 1 and "release_url" not in overrides.load(rid)
    release.poll_release_urls(now=t + 2)                                   # throttled
    assert len(seen) == 1
    answer["url"] = "https://github.com/o/r/releases/tag/v0.2.0"
    release.poll_release_urls(now=t + 2 + release.RELEASE_URL_EVERY_S)
    assert len(seen) == 2 and overrides.load(rid)["release_url"] == answer["url"]
    release.poll_release_urls(now=t + 10 + 2 * release.RELEASE_URL_EVERY_S)  # has a URL
    assert len(seen) == 2


def test_poll_release_urls_gives_up_after_the_poll_window(world, monkeypatch):
    rid = _released(world)
    seen = []
    monkeypatch.setattr(release, "_gh_release_url", lambda rec: seen.append(1) or None)
    t = overrides.load(rid).get("finished_at") or 1_000_000.0
    release.poll_release_urls(now=t + release.RELEASE_URL_POLL_S + 1)
    assert seen == []


def test_poll_release_urls_skips_a_release_that_is_not_released(world, monkeypatch):
    out = _prepare(world)                                       # still pending
    monkeypatch.setattr(release, "_gh_release_url",
                        lambda rec: pytest.fail("only a RELEASED request is polled"))
    t = 1_000_000.0
    overrides.set_fields(out["request_id"], finished_at=t)    # inside the poll window
    release.poll_release_urls(now=t + 1)


# --- orchestrator-added guards (judge round 4 survivors) ----------------------------------

def test_a_failed_release_card_says_what_already_reached_origin():
    """🔴 GUARD: a release that FAILED midway must say what reached origin — never
    "Nothing was pushed" when main and the tag are already out."""
    from chela.telegram.overridewatch import _release_outcome_line
    line = _release_outcome_line({"state": overrides.FAILED, "decided_by": "op",
                                  "detail": "pushing dev failed", "tag": "v0.2.0",
                                  "pushed": ["main", "v0.2.0"]})
    assert "Pushed only: main, v0.2.0" in line and "Nothing was pushed" not in line
    none = _release_outcome_line({"state": overrides.FAILED, "decided_by": "op",
                                  "detail": "x", "tag": "v0.2.0", "pushed": []})
    assert "Nothing was pushed" in none


def test_the_default_sweep_expires_a_pending_release_when_dev_moves(world, pushes):
    """🔴 GUARD (wiring): the daemon/bridge/dashboard call `overrides.sweep()` with NO
    dev_head_of — the default must read origin's dev (release.remote_dev_head)."""
    out = _prepare(world)
    _move_dev(world)
    release._dev_head_cache.clear()
    expired = overrides.sweep()
    assert [r["id"] for r in expired] == [out["request_id"]]
    assert overrides.load(out["request_id"])["state"] == overrides.EXPIRED
    assert pushes == []


def test_a_failed_approval_still_removes_the_release_worktree_and_branch(world, pushes):
    """🔴 GUARD: whatever the outcome — a FAILED approval included — the release's worktree
    and local branch are removed."""
    out = _prepare(world)
    hook = world.origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["state"] == overrides.FAILED
    assert _release_worktrees() == []
    assert _local(world, "refs/heads/chela-release/v0.2.0") is None


def test_the_release_uv_steps_never_inherit_the_callers_venv(monkeypatch):
    """🔴 GUARD: the release worktree's uv steps get their OWN environment."""
    monkeypatch.setenv("VIRTUAL_ENV", "/some/other/.venv")
    assert "VIRTUAL_ENV" not in release._uv_env()


# --- round 5: the sweep's survivors --------------------------------------------------------
# Each test below was written against a mutation of the changed line that left the suite
# green; every one was run RED against its mutation (see the commit message).

def test_the_dashboard_approval_page_says_released_and_names_the_tag(world, pushes):
    """The approve page for a release says "Approved and released" with the tag — NOT the
    "Not released" failure page (which also contains the word "released")."""
    pytest.importorskip("flask")
    from chela.dashboard.app import app
    out = _prepare(world)
    ok = app.test_client().post(f"/override/{out['request_id']}", data={"decision": "approve"})
    assert ok.status_code == 200
    assert b"Approved and released" in ok.data and b"v0.2.0" in ok.data
    assert b"Not released" not in ok.data


def test_the_release_body_file_becomes_the_commit_body(world, tmp_path, capsys):
    from chela import main
    body = tmp_path / "body.md"
    body.write_text("Notes that must reach the commit.\n")
    main.cmd_release(SimpleNamespace(version="0.2.0", theme="t", body_file=str(body),
                                     repo=str(world.work)))
    sha = _local(world, "refs/tags/v0.2.0^{commit}")
    assert "Notes that must reach the commit." in _g(world.work, "log", "-1", "--format=%B", sha)


def test_an_unreadable_body_file_refuses_and_prepares_nothing(world, tmp_path, capsys):
    from chela import main
    with pytest.raises(SystemExit) as exc:
        main.cmd_release(SimpleNamespace(version="0.2.0", theme="t",
                                         body_file=str(tmp_path / "missing.md"),
                                         repo=str(world.work)))
    out = capsys.readouterr().out
    assert exc.value.code == 1 and "REFUSED" in out and "--body-file unreadable" in out
    assert overrides.all_requests() == [] and _local(world, "refs/tags/v0.2.0") is None


def test_merge_approve_cli_reports_a_release_as_released(world, pushes, capsys):
    from chela import main
    out = _prepare(world)
    main.cmd_merge_approve(SimpleNamespace(request=out["request_id"], deny=False))
    printed = capsys.readouterr().out
    assert "approved and released" in printed and out["release_sha"][:12] in printed
    assert len(pushes) == 3


def test_chela_release_is_wired_into_the_command_line(monkeypatch):
    """`chela release 0.2.0 --theme t --body-file b` reaches `cmd_release` with the
    version, theme and body file parsed."""
    from chela import main
    seen = []
    monkeypatch.setattr(main, "cmd_release", lambda args: seen.append(args))
    monkeypatch.setattr("sys.argv", ["chela", "release", "0.2.0", "--theme", "t",
                                     "--body-file", "b.md"])
    main.main()
    (args,) = seen
    assert (args.version, args.theme, args.body_file) == ("0.2.0", "t", "b.md")


def test_not_done_for_an_override_says_nothing_was_merged():
    assert overrides.not_done({"kind": None, "pushed": ["main"]}) == "nothing was merged"
    assert overrides.not_done({"kind": "release"}) == "nothing was pushed"
    assert overrides.not_done({"kind": "release", "pushed": ["main"]}) == "pushed only: main"


def test_an_override_expiry_is_told_as_an_override_not_a_release(told):
    overrides._tell({"id": "r1", "label": "CMX-9", "task_id": "CMX-9"}, overrides.EXPIRED, "x")
    (kind, summary, _), = told
    assert kind == "override_expired" and "override of CMX-9" in summary
    assert "nothing was merged" in summary


def test_a_pending_release_keeps_its_worktree_and_branch(world):
    """Only a request in a FINAL state is cleaned up; a pending one still needs them."""
    out = _prepare(world)
    rec = overrides.load(out["request_id"])
    assert rec["state"] == overrides.PENDING
    overrides._cleanup_release(rec)
    assert len(_release_worktrees()) == 1
    assert _local(world, "refs/heads/chela-release/v0.2.0") is not None
    # control: once final, the same call removes them
    overrides._cleanup_release({**rec, "state": overrides.EXPIRED})
    assert _release_worktrees() == []


def test_a_release_expiry_event_names_the_release_label(world, pushes, told):
    out = _prepare(world)
    _move_dev(world)
    overrides.decide(out["request_id"], True, "terminal:op")
    from chela import event_log
    ev, = event_log.read(types=["orchestrator.release_expired"])["events"]
    assert ev["summary"].startswith(f"{overrides.load(out['request_id'])['label']} expired")
    assert "override of" not in ev["summary"]


def test_a_failed_command_with_no_output_reports_its_exit_code(world, monkeypatch):
    monkeypatch.setattr(release, "_uv_lock",
                        lambda wt: subprocess.CompletedProcess([], 7, "", ""))
    out = _prepare(world)
    assert out["ok"] is False and "exit 7" in out["error"]


def test_tail_keeps_only_the_end_of_long_output():
    p = subprocess.CompletedProcess([], 1, "", "A" * 50 + "THE-END")
    assert release._tail(p, 10) == "AAAAATHE-END"[-10:] and release._tail(p, 10).endswith("THE-END")


@pytest.mark.parametrize("url,slug", [
    ("git@github.com:o/r.git", "o/r"),
    ("https://github.com/o/r", "o/r"),
    ("https://github.com/o/r.git/", "o/r"),
    ("/some/local/bare.git", None),
])
def test_repo_slug_is_read_from_the_origin_url(tmp_path, url, slug):
    _g(tmp_path, "init", "-q")
    _g(tmp_path, "remote", "add", "origin", url)
    assert release._repo_slug(tmp_path) == slug


def test_cleanup_without_a_repository_runs_no_git(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(release, "_git", lambda *a, **k: ran.append(a) or _ok())
    release.cleanup({})
    release.cleanup({"git_dir": str(tmp_path / "gone"), "branch": "b", "tag": "v1"})
    assert ran == []


def test_cleanup_prunes_a_worktree_directory_that_is_already_gone(world):
    out = _prepare(world)
    rec = overrides.load(out["request_id"])
    import shutil
    shutil.rmtree(rec["worktree"])
    release.cleanup({k: v for k, v in rec.items() if k != "worktree"})
    assert "prunable" not in _g(world.work, "worktree", "list", "--porcelain")
    assert _local(world, "refs/heads/chela-release/v0.2.0") is None


def test_unreadable_release_notes_refuse_with_the_reason(world, monkeypatch):
    def boom(*a, **k):
        raise ValueError("no Unreleased heading")
    monkeypatch.setattr(release.release_notes, "promote_unreleased", boom)
    out = _prepare(world)
    assert out["ok"] is False and "release notes: no Unreleased heading" in out["error"]
    assert overrides.all_requests() == []


def test_an_unreadable_uv_lock_refuses_with_the_reason(world, monkeypatch):
    def break_lock(wt: Path) -> subprocess.CompletedProcess:
        (wt / "uv.lock").unlink()
        (wt / "uv.lock").symlink_to(wt / "nowhere")          # changed, but cannot be read
        return _ok()
    monkeypatch.setattr(release, "_uv_lock", break_lock)
    out = _prepare(world)
    assert out["ok"] is False and "uv.lock unreadable" in out["error"]
    assert overrides.all_requests() == []


def test_remote_dev_head_does_not_cache_a_repository_that_is_gone(tmp_path):
    release._dev_head_cache.clear()
    assert release.remote_dev_head({}) is None
    assert release.remote_dev_head({"git_dir": str(tmp_path / "gone")}) is None
    assert release._dev_head_cache == {}


def test_the_telegram_card_tells_each_release_outcome():
    from chela.telegram.overridewatch import _release_outcome_line as line
    base = {"tag": "v0.2.0", "decided_by": "liav"}
    assert line({**base, "state": overrides.MERGING}) == (
        "⏳ Approved by liav — pushing main, v0.2.0, dev…")
    assert line({**base, "state": overrides.DENIED}) == (
        "❌ Denied by liav — nothing was pushed.")
    assert line({**base, "state": overrides.DENIED, "pushed": ["main"]}) == (
        "❌ Denied by liav — pushed only: main.")
    assert line({**base, "state": overrides.EXPIRED, "detail": "dev moved"}) == (
        "⌛ Expired — dev moved. Nothing was pushed.")
    assert line({**base, "state": overrides.PENDING}) is None


# --- round 6: outcome consumers (dashboard + CLI) and the approval page's escaping ---------

def _reject_dev_pushes(world) -> None:
    hook = world.origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nwhile read o n r; do [ \"$r\" = refs/heads/dev ] && "
                    "exit 1; done\nexit 0\n")
    hook.chmod(0o755)


def _post(rid: str, decision: str):
    pytest.importorskip("flask")
    from chela.dashboard.app import app
    return app.test_client().post(f"/override/{rid}", data={"decision": decision})


def test_the_dashboard_page_for_a_release_that_failed_midway_says_what_reached_origin(
        world, pushes):
    """🔴 GUARD: main and the tag are out, dev was rejected — the result page re-reads the
    STORED request after decide (the request fetched before it has no `pushed`) and says so;
    it must never claim nothing was pushed."""
    out = _prepare(world)
    _reject_dev_pushes(world)
    page = _post(out["request_id"], "approve")
    assert page.status_code == 410
    text = page.get_data(as_text=True)
    assert "Not released" in text and "pushing dev failed" in text
    assert "Pushed only: main, v0.2.0" in text
    assert "Nothing was pushed" not in text and "nothing was pushed" not in text
    assert "Approved and released" not in text
    assert _remote(world, "main") == out["release_sha"] and _remote(world, "dev") != out[
        "release_sha"]


def test_the_dashboard_page_for_a_denied_release_says_nothing_was_pushed(world, pushes):
    out = _prepare(world)
    page = _post(out["request_id"], "deny")
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "Denied" in text and "Nothing was pushed" in text
    assert "merged" not in text and "Approved and released" not in text
    assert overrides.load(out["request_id"])["state"] == overrides.DENIED
    assert pushes == []


def test_the_dashboard_page_for_an_expired_release_says_why_and_that_nothing_was_pushed(
        world, pushes):
    out = _prepare(world)
    _move_dev(world)
    page = _post(out["request_id"], "approve")
    text = page.get_data(as_text=True)
    assert page.status_code == 410 and "Not released" in text and "expired" in text
    assert "nothing was pushed" in text.lower()
    assert "Approved and released" not in text
    assert "dev moved" in overrides.load(out["request_id"])["detail"]
    assert pushes == []


@pytest.mark.parametrize("how, word", [("approve", "released"), ("deny", "denied"),
                                       ("expire", "expired")])
def test_the_dashboard_get_for_a_decided_release_offers_no_buttons(world, pushes, how, word):
    """A release that is already released/denied/expired shows its state and no form."""
    pytest.importorskip("flask")
    from chela.dashboard.app import app
    out = _prepare(world)
    if how == "expire":
        _move_dev(world)
    else:
        overrides.decide(out["request_id"], how == "approve", "terminal:op")
    client = app.test_client()
    page = client.get(f"/override/{out['request_id']}")
    text = page.get_data(as_text=True)
    assert page.status_code == 404 and f"It is {word}" in text
    assert "<form" not in text and "Approve release" not in text
    assert client.get("/override/nope").status_code == 404


def _cli_approve(rid: str, *, deny: bool = False):
    from chela import main
    return main.cmd_merge_approve(SimpleNamespace(request=rid, deny=deny))


def test_merge_approve_cli_for_a_release_that_failed_midway_exits_1_and_names_the_pushes(
        world, pushes, capsys):
    out = _prepare(world)
    _reject_dev_pushes(world)
    with pytest.raises(SystemExit) as exc:
        _cli_approve(out["request_id"])
    printed = capsys.readouterr().out
    assert exc.value.code == 1
    assert "pushing dev failed" in printed and "main, v0.2.0" in printed
    assert "approved and released" not in printed


def test_merge_approve_cli_for_an_expired_release_exits_1_and_pushes_nothing(
        world, pushes, capsys):
    out = _prepare(world)
    _move_dev(world)
    with pytest.raises(SystemExit) as exc:
        _cli_approve(out["request_id"])
    printed = capsys.readouterr().out
    assert exc.value.code == 1 and "dev moved" in printed
    assert "approved and released" not in printed and pushes == []


def test_merge_approve_cli_denies_a_release_and_pushes_nothing(world, pushes, capsys):
    out = _prepare(world)
    _cli_approve(out["request_id"], deny=True)          # exit 0: no SystemExit
    printed = capsys.readouterr().out
    assert "denied" in printed and out["request_id"] in printed
    assert "approved" not in printed and pushes == []
    assert overrides.load(out["request_id"])["state"] == overrides.DENIED
    assert _release_worktrees() == []


def test_the_request_records_who_asked_and_what_it_is_bound_to(world):
    """Provenance: kind, version/tag, theme, task ids, actor, and the dev commit it is bound
    to (head_sha) are all stored on the request."""
    dev = _remote(world, "dev")
    out = release.prepare("0.2.0", "a calmer release", repo=world.work,
                          actor="orchestrator:test")
    rec = overrides.load(out["request_id"])
    assert rec["kind"] == "release" and rec["state"] == overrides.PENDING
    assert (rec["version"], rec["tag"], rec["theme"]) == ("0.2.0", "v0.2.0", "a calmer release")
    assert rec["task_ids"] == ["CMX-5", "CMX-7"]
    assert rec["actor"] == "orchestrator:test"
    assert rec["dev_sha"] == rec["head_sha"] == dev
    assert rec["release_sha"] == _local(world, "refs/tags/v0.2.0^{commit}")
    assert rec["release_sha"] != dev


def test_the_cli_passes_the_actor_from_the_environment_to_the_request(world, monkeypatch,
                                                                       capsys):
    from chela import main
    monkeypatch.setenv(config.ACTOR_ENV, "agent:cmx-9")
    main.cmd_release(SimpleNamespace(version="0.2.0", theme="t", body_file=None,
                                     repo=str(world.work)))
    (rec,) = overrides.all_requests()
    assert rec["actor"] == "agent:cmx-9"
    printed = capsys.readouterr().out
    assert "CMX-5, CMX-7" in printed and "tasks (2)" in printed
    assert rec["id"] in printed


def test_the_approval_page_and_the_card_show_every_provenance_field(world):
    pytest.importorskip("flask")
    from chela.dashboard.app import app
    from chela.telegram.overridewatch import format_release_message
    out = release.prepare("0.2.0", "a calmer release", repo=world.work, actor="orch:x")
    rec = overrides.load(out["request_id"])
    text = app.test_client().get(f"/override/{rec['id']}").get_data(as_text=True)
    card = format_release_message(rec)
    for shown in (text, card):
        for needle in ("v0.2.0", "a calmer release", "CMX-5, CMX-7", "orch:x"):
            assert needle in shown, (needle, shown)
    for needle in (rec["dev_sha"], rec["release_sha"]):
        assert needle in text and needle[:12] in card
    assert "Tasks (2)" in text and "Tasks (2)" in card


_PAYLOAD = '<script>alert(1)</script><form action="//evil">'


@pytest.mark.parametrize("field", ["theme", "version", "task_ids", "tag", "actor",
                                   "dev_sha", "release_sha"])
def test_the_release_approval_page_escapes_each_agent_supplied_field(world, field):
    """🔴 GUARD: every field an agent/orchestrator supplies is HTML-escaped on its own — one
    payload per field, the others left benign, so a single un-escaped field cannot hide."""
    pytest.importorskip("flask")
    from chela.dashboard.app import app
    out = _prepare(world)
    value = [f"CMX-1{_PAYLOAD}", "CMX-2"] if field == "task_ids" else _PAYLOAD
    overrides.set_fields(out["request_id"], **{field: value})
    page = app.test_client().get(f"/override/{out['request_id']}")
    text = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "<script>alert(1)" not in text and 'action="//evil"' not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert text.count("<form") == 1                    # only the page's own approve form


# --- round 6: normalization / derivation survivors ----------------------------------------
# Each test below was written against a value/logic mutation that left the suite green and
# was run RED against it (see the commit message). The shape: the old tests fed only the
# already-clean input, so a cleaning step could be deleted unseen.

def _hook_rejecting(world, ref_pattern: str) -> None:
    hook = world.origin / "hooks" / "pre-receive"
    hook.write_text(f"#!/bin/sh\nwhile read o n r; do case \"$r\" in {ref_pattern}) exit 1;; "
                    "esac; done\nexit 0\n")
    hook.chmod(0o755)


def test_a_multi_line_theme_is_collapsed_to_one_line_everywhere(world):
    """🔴 GUARD: the theme is ONE line — newlines, tabs and runs of spaces collapse to single
    spaces in the commit subject, the tag message, the stored record and the card."""
    out = release.prepare("0.2.0", "  a\n calmer \t\n\n release  ", repo=world.work)
    assert out["ok"] is True, out
    sha = out["release_sha"]
    assert _g(world.work, "log", "-1", "--format=%s", sha) == "Release 0.2.0 — a calmer release"
    assert _g(world.work, "cat-file", "-p", "v0.2.0").splitlines()[-1] == \
        "0.2.0 — a calmer release"
    rec = overrides.load(out["request_id"])
    assert rec["theme"] == rec["title"] == out["theme"] == "a calmer release"
    assert out["question"].startswith("Release v0.2.0 — a calmer release? Cut from dev ")
    from chela.telegram.overridewatch import format_release_message
    assert "a calmer release" in format_release_message(rec)


def test_a_none_theme_or_version_refuses_instead_of_crashing(world):
    assert "--theme" in release.prepare("0.2.0", None, repo=world.work)["error"]
    assert "not a release version" in release.prepare(None, "t", repo=world.work)["error"]


@pytest.mark.parametrize("given", ["v0.2.0", " 0.2.0 ", " v0.2.0\n", "0.2.0"])
def test_a_version_is_cleaned_to_the_same_release(world, given):
    """🔴 GUARD: `v0.2.0`, ` 0.2.0 ` and `0.2.0` are ONE release: version `0.2.0`, tag
    `v0.2.0` — never `vv0.2.0`, never refused as not-a-version."""
    out = release.prepare(given, "t", repo=world.work)
    assert out["ok"] is True, out
    assert out["version"] == "0.2.0" and out["tag"] == "v0.2.0"
    assert _local(world, "refs/tags/v0.2.0") and _local(world, "refs/tags/vv0.2.0") is None
    assert _g(world.work, "log", "-1", "--format=%s", out["release_sha"]) == "Release 0.2.0 — t"
    assert 'version = "0.2.0"' in _g(world.work, "show", f"{out['release_sha']}:pyproject.toml")


@pytest.mark.parametrize("given", ["vv0.2.0", "V0.2.0", "0.2", "0.2.0.1", "0.2.0-rc1",
                                   "0.2.x", "v", ""])
def test_only_one_leading_lowercase_v_is_stripped(world, given):
    out = release.prepare(given, "t", repo=world.work)
    assert out["ok"] is False and "not a release version" in out["error"]
    assert overrides.all_requests() == [] and _release_worktrees() == []


def test_versions_compare_numerically_not_as_text():
    assert release._version_tuple("0.10.0") > release._version_tuple("0.9.0")
    assert release._version_tuple("1.0.0") > release._version_tuple("0.99.99")
    assert release._version_tuple("0.2.0") == (0, 2, 0)
    assert release._version_tuple("0.2") is None and release._version_tuple(None) is None


def test_task_ids_are_unique_in_first_appearance_order():
    """🔴 GUARD: an id mentioned twice appears once, at its FIRST position."""
    section = "- (CMX-7)\n- (CMX-5, CMX-7)\n- again CMX-5 and CMX-12, CMX-7\n"
    assert release.task_ids(section) == ["CMX-7", "CMX-5", "CMX-12"]


def test_task_ids_match_whole_ids_case_insensitively_and_normalise_the_case():
    assert release.task_ids("see cmx-9, Cmx-9 and (CMX-10)") == ["CMX-9", "CMX-10"]
    assert release.task_ids("XCMX-4 CMX-4a CMX-") == []
    assert release.task_ids("") == [] and release.task_ids(None) == []


def test_the_cards_task_count_and_list_do_not_repeat_an_id_mentioned_twice(world):
    """🔴 GUARD: end to end — fragments mentioning CMX-5 three times give ONE id, the count
    in the question says 2, and the stored record lists each once."""
    (world.work / "changelog.d" / "CMX-5.md").write_text(
        "### Added\n\n- **Five.** (CMX-5, #10)\n- **Five again.** (CMX-5)\n"
        "- **Both.** (CMX-7, CMX-5)\n")
    _g(world.work, "add", "-A")
    _g(world.work, "commit", "-q", "-m", "more")
    _g(world.work, "push", "-q", "origin", "dev")
    out = _prepare(world)
    assert out["ok"], out
    assert out["task_ids"] == ["CMX-5", "CMX-7"]
    assert out["question"].endswith("2 task(s): CMX-5, CMX-7")
    assert overrides.load(out["request_id"])["task_ids"] == ["CMX-5", "CMX-7"]


def test_a_release_with_no_task_ids_says_none(world):
    (world.work / "changelog.d" / "CMX-5.md").write_text("### Added\n\n- **Five.**\n")
    (world.work / "changelog.d" / "CMX-7.md").write_text("### Fixed\n\n- **Seven.**\n")
    _g(world.work, "add", "-A")
    _g(world.work, "commit", "-q", "-m", "anon")
    _g(world.work, "push", "-q", "origin", "dev")
    out = _prepare(world)
    assert out["ok"] and out["task_ids"] == []
    assert out["question"].endswith("0 task(s): none")


def test_the_question_and_the_hint_are_exactly_worded(world):
    out = _prepare(world)
    assert out["question"] == (f"Release v0.2.0 — a calmer release? Cut from dev "
                               f"{out['dev_sha'][:12]}; 2 task(s): CMX-5, CMX-7")
    rid = out["request_id"]
    assert out["hint"] == release.approve_hint(rid) == (
        f"Approve with the Telegram button, on the dashboard (/override/{rid}), "
        f"or in a plain terminal: chela merge-approve {rid}  (deny: add --deny)")


def test_the_release_tests_command_leaves_no_pytest_cache(monkeypatch, tmp_path):
    """🔴 GUARD: the REAL release-tests command, run for real (minus the `uv` wrapper), must
    not create `.pytest_cache` in its cwd — that would dirty the release worktree and refuse
    every real release. Its uv wrapper flags are pinned too."""
    seen = {}
    real_run = subprocess.run                    # `release.subprocess` IS this module

    def spy(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(release.subprocess, "run", spy)
    monkeypatch.setenv("VIRTUAL_ENV", "/some/other/.venv")
    _REAL_RELEASE_TESTS(tmp_path)
    cmd = seen["cmd"]
    assert cmd[:7] == ["uv", "run", "--frozen", "--extra", "dev", "python", "-m"]
    assert seen["kw"]["cwd"] == tmp_path and seen["kw"]["timeout"] == 900
    assert "VIRTUAL_ENV" not in seen["kw"]["env"]
    assert seen["kw"]["env"] is not None and seen["kw"]["env"]["PATH"] == os.environ["PATH"]
    assert seen["kw"]["capture_output"] is True and seen["kw"]["text"] is True
    assert cmd[-len(release.RELEASE_TESTS):] == list(release.RELEASE_TESTS)

    import sys
    clean = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}      # no ambient pytest/venv
    for rel in release.RELEASE_TESTS:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("def test_ok():\n    pass\n")
    real = real_run([sys.executable, *cmd[cmd.index("python") + 1:]], cwd=tmp_path,
                          capture_output=True, text=True, env=clean)
    assert real.returncode == 0, real.stdout + real.stderr
    assert not (tmp_path / ".pytest_cache").exists()
    assert real_run([sys.executable, "-m", "pytest", "-q", *release.RELEASE_TESTS],
                    cwd=tmp_path, capture_output=True, env=clean).returncode == 0
    assert (tmp_path / ".pytest_cache").exists(), "control: without the flag a cache IS made"


def test_the_commit_body_is_the_stripped_body_and_an_empty_body_adds_none(world):
    out = release.prepare("0.2.0", "t", body="\n\n  Longer notes.\nSecond.\n\n", repo=world.work)
    assert _g(world.work, "log", "-1", "--format=%B", out["release_sha"]) == \
        "Release 0.2.0 — t\n\nLonger notes.\nSecond."


@pytest.mark.parametrize("body", ["", "   \n ", None])
def test_a_blank_body_leaves_a_subject_only_commit(world, body):
    out = release.prepare("0.2.0", "t", body=body, repo=world.work)
    assert out["ok"], out
    assert _g(world.work, "log", "-1", "--format=%B", out["release_sha"]) == "Release 0.2.0 — t"


def test_the_release_files_are_rewritten_exactly(world):
    """The pyproject changes ONLY its version line; the CHANGELOG section is dated today;
    the worktree sits under CHELA_DIR/releases as `<tag>-<6 hex>` on `chela-release/<tag>`."""
    before = __import__("datetime").date.today().isoformat()
    out = _prepare(world)
    sha = out["release_sha"]
    after = __import__("datetime").date.today().isoformat()
    assert _g(world.work, "show", f"{sha}:pyproject.toml") + "\n" == \
        PYPROJECT.replace('version = "0.1.0"', 'version = "0.2.0"')
    m = re.search(r"(?m)^## \[0\.2\.0\] — (\S+)$", _g(world.work, "show", f"{sha}:CHANGELOG.md"))
    assert m and m.group(1) in {before, after}
    rec = overrides.load(out["request_id"])
    wt = Path(rec["worktree"])
    assert wt.parent == config.CHELA_DIR / "releases"
    assert re.fullmatch(r"v0\.2\.0-[0-9a-f]{6}", wt.name)
    assert rec["branch"] == "chela-release/v0.2.0"
    assert _g(wt, "rev-parse", "--abbrev-ref", "HEAD") == "chela-release/v0.2.0"


def test_a_refused_tag_push_keeps_the_local_tag_so_the_operator_can_push_it(world, pushes,
                                                                            told):
    """🔴 GUARD (pins the CURRENT rule): `main` landed (pushed == ['main']) but the TAG push
    was refused ⇒ the request FAILS, the local tag is KEPT, the worktree and branch go."""
    out = _prepare(world)
    _hook_rejecting(world, "refs/tags/*")
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["ok"] is False and res["state"] == overrides.FAILED, res
    assert "pushing v0.2.0 failed" in res["detail"] and "pushed so far: main" in res["detail"]
    assert overrides.load(out["request_id"])["pushed"] == ["main"]
    assert _remote(world, "refs/tags/v0.2.0") is None
    assert _local(world, "refs/tags/v0.2.0^{commit}") == out["release_sha"]
    assert _local(world, "refs/heads/chela-release/v0.2.0") is None
    assert _release_worktrees() == []


def test_a_refused_main_push_removes_the_unpushed_local_tag(world, pushes, told):
    out = _prepare(world)
    _hook_rejecting(world, "refs/heads/main")
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["state"] == overrides.FAILED
    assert overrides.load(out["request_id"])["pushed"] == []
    assert _local(world, "refs/tags/v0.2.0") is None
    assert _local(world, "refs/heads/chela-release/v0.2.0") is None


def test_cleanup_keeps_a_tag_that_is_not_ours_or_when_told_to(world):
    out = _prepare(world)
    rec = dict(overrides.load(out["request_id"]))
    release.cleanup({**rec, "release_sha": world.work and _remote(world, "dev")})
    assert _local(world, "refs/tags/v0.2.0") is not None, "a same-named tag elsewhere stays"
    assert _local(world, "refs/heads/chela-release/v0.2.0") is None
    # now ours, but keep_tag=True
    release.cleanup(rec, keep_tag=True)
    assert _local(world, "refs/tags/v0.2.0") is not None
    release.cleanup(rec)                                         # default: not kept
    assert _local(world, "refs/tags/v0.2.0") is None


def test_the_request_event_names_the_tag_and_theme(world, monkeypatch):
    seen = []
    monkeypatch.setattr(release.event_log, "append",
                        lambda kind, msg, payload=None: seen.append((kind, msg, payload)))
    out = _prepare(world)
    (kind, msg, payload), = seen
    assert kind == "orchestrator.release_requested"
    assert msg == "release v0.2.0 requested: a calmer release"
    assert payload["id"] == out["request_id"] and "telegram" not in payload


def test_the_notification_carries_the_question_hint_and_title(world, monkeypatch):
    sent = []
    monkeypatch.setattr(release.notify, "enabled", lambda: True)
    monkeypatch.setattr(release.notify, "send", lambda text, title=None: sent.append((text, title)))
    out = _prepare(world)
    assert sent == [(f"{out['question']}\n\n{out['hint']}", "chela: approve release v0.2.0?")]


def test_remote_dev_head_cache_is_fresh_strictly_inside_max_age(world, monkeypatch):
    calls = []
    real = release._git

    def spy(cwd, *args, **kw):
        calls.append(args[:1])
        return real(cwd, *args, **kw)
    monkeypatch.setattr(release, "_git", spy)
    clock = {"t": 1000.0}
    monkeypatch.setattr(release.time, "monotonic", lambda: clock["t"])
    rec = {"git_dir": str(world.work)}
    release.remote_dev_head(rec, max_age=60)
    clock["t"] += 59.9
    release.remote_dev_head(rec, max_age=60)
    assert calls == [("ls-remote",)]
    clock["t"] += 0.1                                            # exactly max_age: stale
    release.remote_dev_head(rec, max_age=60)
    assert calls == [("ls-remote",)] * 2


def test_poll_release_urls_window_and_throttle_boundaries(world, monkeypatch):
    rid = _released(world)
    seen = []
    monkeypatch.setattr(release, "_gh_release_url", lambda rec: seen.append(1) or None)
    overrides.set_fields(rid, finished_at=1000.0, release_url_checked=0)
    release.poll_release_urls(now=1000.0 + release.RELEASE_URL_POLL_S)      # edge: still in
    assert len(seen) == 1
    overrides.set_fields(rid, release_url_checked=5000.0)
    release.poll_release_urls(now=5000.0 + release.RELEASE_URL_EVERY_S - 0.1)
    assert len(seen) == 1
    release.poll_release_urls(now=5000.0 + release.RELEASE_URL_EVERY_S)     # edge: due
    assert len(seen) == 2


# --- round 7: the staged-file hole in the dirty check --------------------------------------

def test_a_step_that_stages_a_stray_file_outside_the_release_paths_refuses(world, monkeypatch):
    """🔴 GUARD: a file a step STAGED (`git add`) outside the release paths reads as clean in
    the worktree — `git commit` would ship it. It must refuse, naming the file."""
    (world.work / "other.py").write_text("x = 1\n")
    _g(world.work, "add", "other.py")
    _g(world.work, "commit", "-q", "-m", "other")
    _g(world.work, "push", "-q", "origin", "dev")

    def stage(wt):
        (wt / "stray.txt").write_text("x")
        _g(wt, "add", "stray.txt")                              # staged, clean in the worktree
        (wt / "other.py").write_text("x = 2\n")
        _g(wt, "add", "other.py")                               # a staged MODIFICATION
        return _ok()
    monkeypatch.setattr(release, "_release_tests", stage)
    out = _prepare(world)
    _assert_refused_and_clean(world, out, why="dirty")
    assert "stray.txt" in out["error"] and "other.py" in out["error"]


def test_staged_changes_inside_the_release_paths_are_allowed(world, monkeypatch):
    """The release's own files are staged by `git add` before the check — that must pass,
    and the commit holds exactly them."""
    def stage_own(wt):
        (wt / "changelog.d" / "CMX-9.md").write_text("### Added\n\n- **Nine.** (CMX-9)\n")
        _g(wt, "add", "changelog.d/CMX-9.md")
        return _ok()
    monkeypatch.setattr(release, "_release_tests", stage_own)
    out = _prepare(world)
    assert out["ok"] is True, out
    files = set(_g(world.work, "show", "--name-only", "--format=", out["release_sha"]).split())
    assert files <= {"CHANGELOG.md", "pyproject.toml", "uv.lock"} | {
        f for f in files if f.startswith("changelog.d/")}
    assert "CHANGELOG.md" in files and "pyproject.toml" in files


# --- round 7: release-vs-override kind discrimination in chela.overrides -------------------
# Every `_is_release(rec)` branch gets BOTH kinds through it, asserting the kind-specific
# observable (event name, text, state, cleanup, which seam the sweep reads).

from chela import event_log  # noqa: E402


def _override(**kw) -> dict:
    """A pending merge-OVERRIDE request (no kind), as contract.merge(override=True) records."""
    rec = overrides.create({"task_id": "CMX-9", "label": "CMX-9", "head_sha": "a" * 40,
                            "pr_url": "https://github.com/o/r/pull/9", "base": "dev",
                            "question": "Override-merge CMX-9?", **kw})
    assert rec is not None
    return rec


def _events(*types: str) -> list[dict]:
    return event_log.read(types=list(types))["events"]


@pytest.fixture
def sent(monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(overrides.notify, "enabled", lambda: True)
    monkeypatch.setattr(overrides.notify, "send",
                        lambda text, title=None, **k: seen.append((text, title)))
    return seen


def _complete_override(monkeypatch, outcome: dict):
    from chela import contract
    monkeypatch.setattr(contract, "complete_override", lambda rec, by: outcome)


def test_an_approved_release_writes_the_release_pushed_audit_event(world, pushes):
    out = _prepare(world)
    assert overrides.decide(out["request_id"], True, "terminal:op")["state"] == overrides.RELEASED
    ev, = _events("orchestrator.release_pushed")
    assert ev["payload"]["approved_by"] == "terminal:op"
    assert ev["payload"]["tag"] == "v0.2.0" and ev["payload"]["release_sha"] == out["release_sha"]
    assert ev["payload"]["request_id"] == out["request_id"]
    assert "APPROVED by terminal:op and pushed" in ev["summary"]
    assert _events("orchestrator.merge_override_pushed") == []


def test_a_denied_release_and_a_denied_override_each_log_their_own_event(world, pushes):
    rel = _prepare(world)
    ovr = _override()
    overrides.decide(rel["request_id"], False, "terminal:op")
    overrides.decide(ovr["id"], False, "terminal:op")
    r, = _events("orchestrator.release_denied")
    o, = _events("orchestrator.merge_override_denied")
    assert r["payload"]["id"] == rel["request_id"] and r["payload"]["approved_by"] == "terminal:op"
    assert o["payload"]["id"] == ovr["id"] and o["payload"]["approved_by"] == "terminal:op"
    assert r["summary"] == f"{overrides.load(rel['request_id'])['label']} DENIED by terminal:op"
    assert o["summary"] == "override of CMX-9 DENIED by terminal:op"


def test_a_failed_release_and_a_failed_override_each_log_and_tell_their_own(
        world, pushes, told, sent, monkeypatch):
    rel = _prepare(world)
    sent.clear()                                        # the approval request's own push
    _reject_dev_pushes(world)
    overrides.decide(rel["request_id"], True, "terminal:op")
    ovr = _override()
    _complete_override(monkeypatch, {"ok": False, "state": overrides.FAILED, "detail": "boom"})
    overrides.decide(ovr["id"], True, "terminal:op")

    r, = _events("orchestrator.release_failed")
    assert r["summary"].startswith("release v0.2.0 approved by terminal:op but NOT released: ")
    o, = _events("orchestrator.merge_override_failed")
    assert o["summary"] == "override of CMX-9 approved by terminal:op but NOT merged: boom"

    detail = overrides.load(rel["request_id"])["detail"]
    assert sent == [
        (f"release v0.2.0 failed: {detail}\n\nPushed only: main, v0.2.0.", "chela: release failed"),
        ("override of CMX-9 failed: boom\n\nNothing was merged.", "chela: override failed"),
    ]
    assert [k for k, _s, _p in told] == ["release_failed", "override_failed"]
    assert told[0][1].startswith("🏷️ ") and told[1][1].startswith("⚖️ ")
    assert told[0][1].endswith("— pushed only: main, v0.2.0")
    assert told[1][1].endswith("— nothing was merged")


def test_a_release_that_pushed_nothing_says_so_never_nothing_was_merged(world, pushes, sent):
    out = _prepare(world)
    sent.clear()
    hook = world.origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    overrides.decide(out["request_id"], True, "terminal:op")
    (text, title), = sent
    assert text.endswith("\n\nNothing was pushed.") and "merged" not in text
    assert title == "chela: release failed"


def test_an_expired_release_and_an_expired_override_each_log_and_tell_their_own(
        world, pushes, sent, told):
    rel = _prepare(world)
    sent.clear()
    ovr = _override()
    overrides._expire(overrides.load(rel["request_id"]), "dev moved")
    overrides._expire(overrides.load(ovr["id"]), "head moved")
    r, = _events("orchestrator.release_expired")
    o, = _events("orchestrator.merge_override_expired")
    assert r["summary"] == "release v0.2.0 expired: dev moved"
    assert o["summary"] == "override of CMX-9 expired: head moved"
    assert sent == [("release v0.2.0 expired: dev moved\n\nNothing was pushed.", "chela: release expired"),
                    ("override of CMX-9 expired: head moved\n\nNothing was merged.",
                     "chela: override expired")]
    assert [k for k, _s, _p in told] == ["release_expired", "override_expired"]
    assert _release_worktrees() == []                       # the release's worktree went too


def test_an_approved_override_is_merged_and_never_records_pushed_refs(monkeypatch):
    ovr = _override()
    _complete_override(monkeypatch, {"ok": True, "merge_commit_sha": "m" * 40,
                                     "pushed": ["main"]})
    res = overrides.decide(ovr["id"], True, "terminal:op")
    assert res["ok"] is True and res["state"] == overrides.MERGED
    rec = overrides.load(ovr["id"])
    assert rec["state"] == overrides.MERGED and "pushed" not in rec
    assert _events("orchestrator.release_pushed") == []


def test_an_approved_release_ends_released_and_records_what_was_pushed(world, pushes):
    out = _prepare(world)
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["state"] == overrides.RELEASED
    assert overrides.load(out["request_id"])["pushed"] == ["main", "v0.2.0", "dev"]


def test_only_a_release_in_a_final_state_is_cleaned_up(monkeypatch):
    calls = []
    monkeypatch.setattr(release, "cleanup", lambda rec, keep_tag=False: calls.append(
        (rec["id"], keep_tag)))
    for state in overrides.FINAL_STATES:
        overrides._cleanup_release({"id": f"ovr-{state}", "state": state})        # override
    assert calls == []
    overrides._cleanup_release({"id": "rel-released", "kind": "release",
                                "state": overrides.RELEASED})
    overrides._cleanup_release({"id": "rel-main", "kind": "release",
                                "state": overrides.FAILED, "pushed": ["main"]})
    overrides._cleanup_release({"id": "rel-none", "kind": "release",
                                "state": overrides.DENIED})
    assert calls == [("rel-released", True), ("rel-main", True), ("rel-none", False)]


def test_a_denied_override_runs_no_release_cleanup(world, pushes, monkeypatch):
    calls = []
    monkeypatch.setattr(release, "cleanup", lambda rec, keep_tag=False: calls.append(rec["id"]))
    overrides.decide(_override()["id"], False, "terminal:op")
    assert calls == []


def test_the_sweep_reads_dev_for_a_release_and_the_pr_head_for_an_override(world, pushes):
    rel = _prepare(world)
    ovr = _override()
    seen = {"dev": [], "pr": []}

    def dev_head_of(rec):
        seen["dev"].append(rec["id"])
        return "d" * 40

    def head_of(task_id):
        seen["pr"].append(task_id)
        return "e" * 40
    expired = overrides.sweep(head_of=head_of, dev_head_of=dev_head_of)
    assert seen == {"dev": [rel["request_id"]], "pr": ["CMX-9"]}
    by_id = {r["id"]: r for r in expired}
    assert by_id[rel["request_id"]]["detail"].startswith("dev moved (")
    assert "cut it again from the new dev" in by_id[rel["request_id"]]["detail"]
    assert by_id[ovr["id"]]["detail"].startswith("the PR head moved (")
    assert "request a new override" in by_id[ovr["id"]]["detail"]


def test_prepare_is_blocked_only_by_a_pending_release_never_by_a_merge_override(world):
    ovr = _override()
    out = _prepare(world)
    assert out["ok"] is True, out                       # a pending OVERRIDE does not block
    assert {r["id"] for r in overrides.pending()} == {ovr["id"], out["request_id"]}
    again = _prepare(world, version="0.3.0")            # a pending RELEASE does
    assert again["ok"] is False and "already waiting" in again["error"]
    assert out["request_id"] in again["error"] and "v0.2.0" in again["error"]


def test_a_stray_file_that_merely_starts_like_a_release_path_still_refuses(world, monkeypatch):
    """A prefix is not containment: `changelog.dx/` and `CHANGELOG.md.bak` are NOT inside
    `changelog.d` / `CHANGELOG.md`."""
    def stray(wt):
        (wt / "CHANGELOG.md.bak").write_text("x")
        (wt / "changelog.dx").mkdir()
        (wt / "changelog.dx" / "a.md").write_text("x")
        return _ok()
    monkeypatch.setattr(release, "_release_tests", stray)
    out = _prepare(world)
    _assert_refused_and_clean(world, out, why="dirty")
    assert "CHANGELOG.md.bak" in out["error"] and "changelog.dx/a.md" in out["error"]


def test_poll_release_urls_never_looks_up_a_merge_override(monkeypatch):
    ovr = _override()
    overrides.set_fields(ovr["id"], state=overrides.RELEASED, finished_at=1000.0)
    monkeypatch.setattr(release, "_gh_release_url",
                        lambda rec: pytest.fail("only a RELEASE is polled for a URL"))
    release.poll_release_urls(now=1001.0)


# --- round 9: how a push is spelled, unreadable remote reads, and WHICH record complete() reads
# Each test below is the negative control's mirror: the same world with no violation pushes
# main -> tag -> dev (test_approve_pushes_main_tag_dev_in_order_exactly_once).

@pytest.fixture
def argvs(monkeypatch):
    """The FULL argv of every `git push` chela.release ran (the real push still happens)."""
    seen: list[tuple] = []
    real = release._git

    def spy(cwd, *args, **kw):
        if args and args[0] == "push":
            seen.append(tuple(args))
        return real(cwd, *args, **kw)
    monkeypatch.setattr(release, "_git", spy)
    return seen


def test_every_push_is_spelled_plain_with_no_force_flag_and_no_plus_refspec(world, argvs):
    """🔴 GUARD: the WHOLE argv of each push is pinned — `--force`, `-f`, `--force-with-lease`,
    `--mirror`, `--delete`, a `+` refspec or any other added flag changes it ⇒ RED."""
    out = _prepare(world)
    sha = out["release_sha"]
    assert overrides.decide(out["request_id"], True, "terminal:op")["state"] == overrides.RELEASED
    assert argvs == [("push", "--quiet", "origin", f"{sha}:refs/heads/main"),
                     ("push", "--quiet", "origin", "refs/tags/v0.2.0:refs/tags/v0.2.0"),
                     ("push", "--quiet", "origin", f"{sha}:refs/heads/dev")]
    for argv in argvs:
        assert not any(a in ("-f", "--force", "--force-with-lease", "--mirror", "--delete")
                       or a.startswith(("--force", "+")) for a in argv)


def _diverge(world, ref: str) -> str:
    """Point `ref` in the bare origin at a parentless commit (a non-ancestor of anything)."""
    ident = ("-c", "user.email=o@example.com", "-c", "user.name=O")
    empty = subprocess.run(["git", "-C", str(world.origin), "mktree"], input="",
                           check=True, capture_output=True, text=True).stdout.strip()
    sha = _g(world.origin, *ident, "commit-tree", empty, "-m", f"rewritten {ref}")
    _g(world.origin, "update-ref", ref, sha)
    return sha


@pytest.mark.parametrize("nth, ref, what", [(0, "refs/heads/main", "main"),
                                            (1, "refs/tags/v0.2.0", "v0.2.0"),
                                            (2, "refs/heads/dev", "dev")])
def test_a_ref_that_moved_to_a_non_ancestor_during_the_pushes_is_refused_by_git(
        world, monkeypatch, told, nth, ref, what):
    """🔴 GUARD (real bare origin): the ref the push is about to update is rewritten on origin
    to a commit that is NOT an ancestor of the release, right before that push. A plain push is
    refused by git (non-fast-forward / tag exists); a forced one would overwrite it. The
    release reports the refusal and origin keeps the other party's commit."""
    out = _prepare(world)
    sha = out["release_sha"]
    real = release._git
    count = {"n": 0}
    theirs: dict[str, str] = {}

    def racing(cwd, *args, **kw):
        if args and args[0] == "push":
            if count["n"] == nth:
                theirs["sha"] = _diverge(world, ref)
            count["n"] += 1
        return real(cwd, *args, **kw)
    monkeypatch.setattr(release, "_git", racing)
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["ok"] is False and res["state"] == overrides.FAILED, res
    assert f"pushing {what} failed" in res["detail"], res
    assert _remote(world, f"{ref}^{{commit}}") == theirs["sha"] != sha      # NOT overwritten
    assert overrides.load(out["request_id"])["pushed"] == ["main", "v0.2.0", "dev"][:nth]
    assert [k for k, *_ in told] == ["release_failed"]


def test_origin_main_unreadable_at_approval_fails_closed(world, pushes, told, monkeypatch):
    """🔴 GUARD: `origin/main` reading as None at approval is NOT 'nothing to fast-forward
    from': nothing is pushed, the reason names the unreadable main, and the cleanup rules run
    (the unpushed local tag and branch go)."""
    out = _prepare(world)
    main0, dev0 = _remote(world, "main"), _remote(world, "dev")
    real = release._rev
    monkeypatch.setattr(release, "_rev", lambda cwd, ref: None
                        if ref == "refs/remotes/origin/main" else real(cwd, ref))
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["ok"] is False and res["state"] == overrides.FAILED, res
    assert "origin/main (?)" in res["detail"] and "nothing pushed" in res["detail"]
    _assert_origin_untouched(world, main0, dev0, pushes)
    assert [k for k, *_ in told] == ["release_failed"] and "nothing was pushed" in told[0][1]
    assert _local(world, "refs/tags/v0.2.0") is None
    assert _local(world, "refs/heads/chela-release/v0.2.0") is None and _release_worktrees() == []


def test_origin_dev_unreadable_at_approval_expires_and_pushes_nothing(world, pushes, told,
                                                                      monkeypatch):
    out = _prepare(world)
    main0, dev0 = _remote(world, "main"), _remote(world, "dev")
    real = release._rev
    monkeypatch.setattr(release, "_rev", lambda cwd, ref: None
                        if ref == "refs/remotes/origin/dev" else real(cwd, ref))
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["ok"] is False and res["state"] == overrides.EXPIRED, res
    assert "dev moved" in res["detail"] and "→ ?" in res["detail"]
    _assert_origin_untouched(world, main0, dev0, pushes)


def test_a_missing_local_tag_at_approval_pushes_nothing(world, pushes):
    out = _prepare(world)
    main0, dev0 = _remote(world, "main"), _remote(world, "dev")
    _g(world.work, "tag", "-d", "v0.2.0")
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["state"] == overrides.FAILED and "no longer points" in res["detail"], res
    _assert_origin_untouched(world, main0, dev0, pushes)


def test_an_unreadable_release_parent_at_approval_pushes_nothing(world, pushes, monkeypatch):
    out = _prepare(world)
    main0, dev0 = _remote(world, "main"), _remote(world, "dev")
    sha, real = out["release_sha"], release._rev
    monkeypatch.setattr(release, "_rev", lambda cwd, ref: None
                        if ref == f"{sha}^" else real(cwd, ref))
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["state"] == overrides.FAILED and "single commit" in res["detail"], res
    _assert_origin_untouched(world, main0, dev0, pushes)


# --- complete() reads every value from the STORED request ---------------------------------

def _approved_with_a_caller_copy(world, **lies):
    """A request moved to `merging` on disk, plus a caller's in-memory copy that disagrees on
    the fields in ``lies``."""
    out = _prepare(world)
    overrides.set_fields(out["request_id"], state=overrides.MERGING)
    stored = overrides.load(out["request_id"])
    return out, stored, {**stored, **lies}


def test_complete_takes_dev_and_release_shas_from_disk_never_the_callers_copy(world, pushes):
    """🔴 GUARD: a stale/tampered caller record names bogus dev/release commits. complete()
    pushes the STORED release commit and never the caller's."""
    out, stored, lie = _approved_with_a_caller_copy(
        world, dev_sha="f" * 40, release_sha="e" * 40)
    res = release.complete(lie, "terminal:op")
    assert res["ok"] is True and res["merge_commit_sha"] == stored["release_sha"], res
    assert pushes == [f"{stored['release_sha']}:refs/heads/main",
                      "refs/tags/v0.2.0:refs/tags/v0.2.0",
                      f"{stored['release_sha']}:refs/heads/dev"]
    assert "e" * 40 not in " ".join(pushes)
    assert _remote(world, "main") == _remote(world, "dev") == stored["release_sha"]


def test_a_caller_copy_naming_a_real_but_different_release_commit_cannot_redirect_the_push(
        world, pushes):
    """The caller's copy points release_sha at a REAL other commit (dev's head, which the
    tag and parent checks would refuse if read). Stored values win: the approved commit
    ships, the other commit is never pushed as main."""
    out, stored, lie = _approved_with_a_caller_copy(
        world, release_sha=_remote(world, "dev"))
    res = release.complete(lie, "terminal:op")
    assert res["ok"] is True and res["merge_commit_sha"] == stored["release_sha"], res
    assert _remote(world, "main") == stored["release_sha"] != lie["release_sha"]


def test_complete_reads_every_field_from_the_stored_request(world, pushes):
    """🔴 GUARD: id/git_dir/tag/version/task_ids/dev_sha/release_sha all come from disk. The
    caller's copy lies about each; the push, the audit event and the result follow the stored
    request."""
    out, stored, lie = _approved_with_a_caller_copy(
        world, git_dir=str(world.tmp / "nowhere"), tag="v9.9.9", version="9.9.9",
        task_ids=["ZZZ-1"], dev_sha="f" * 40, release_sha="e" * 40)
    res = release.complete(lie, "terminal:op")
    assert res["ok"] is True and res["pushed"] == ["main", "v0.2.0", "dev"], res
    assert _remote(world, "refs/tags/v0.2.0^{commit}") == stored["release_sha"]
    assert _remote(world, "refs/tags/v9.9.9") is None
    ev, = _events("orchestrator.release_pushed")
    assert ev["payload"] == {"request_id": stored["id"], "tag": "v0.2.0", "version": "0.2.0",
                             "release_sha": stored["release_sha"], "dev_sha": stored["dev_sha"],
                             "approved_by": "terminal:op", "task_ids": ["CMX-5", "CMX-7"]}
    assert ev["summary"] == (f"release v0.2.0 APPROVED by terminal:op and pushed "
                             f"({stored['release_sha'][:12]})")


# --- the operator's words, at every notify call site, for both kinds ----------------------

@pytest.mark.parametrize("approve", [True, False])
def test_an_approved_or_denied_release_sends_no_failure_notification(world, pushes, sent,
                                                                     approve):
    out = _prepare(world)
    sent.clear()                                        # the approval request's own push
    assert overrides.decide(out["request_id"], approve, "terminal:op")["ok"] is True
    assert sent == []


def test_each_request_notification_names_its_own_kind_in_text_and_title(world, sent):
    """The release REQUEST push says release in question and title (the override request's
    mirror lives in test_merge_override.py)."""
    rel = _prepare(world)
    assert sent == [(f"{rel['question']}\n\n{rel['hint']}", "chela: approve release v0.2.0?")]
    assert rel["question"].startswith("Release v0.2.0 — ") and "override" not in rel["question"]


# --- CMX-87 round 10: every subprocess seam, pinned on its SUCCESS path -------------------

def _spy_run(monkeypatch):
    seen = []

    def spy(cmd, **kw):
        seen.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(release.subprocess, "run", spy)
    return seen


def test_uv_lock_runs_in_the_release_worktree_with_its_own_environment(monkeypatch, tmp_path):
    """🔴 GUARD: `uv lock` on a real run: exact argv, cwd = the release worktree, a 600s
    timeout, captured text output, and an env WITHOUT the caller's VIRTUAL_ENV (mutation
    `env=None` would inherit it)."""
    seen = _spy_run(monkeypatch)
    monkeypatch.setenv("VIRTUAL_ENV", "/caller/.venv")
    monkeypatch.setenv("CMX87_MARKER", "kept")
    assert _REAL_UV_LOCK(tmp_path).returncode == 0
    (cmd, kw), = seen
    assert cmd == ["uv", "lock"]
    assert kw["cwd"] == tmp_path and kw["timeout"] == 600
    assert kw["capture_output"] is True and kw["text"] is True
    assert kw["env"] is not None and "VIRTUAL_ENV" not in kw["env"]
    assert kw["env"]["CMX87_MARKER"] == "kept"          # the rest of the environment is kept


def test_the_caller_environment_is_never_mutated_by_the_uv_env(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/caller/.venv")
    assert "VIRTUAL_ENV" not in release._uv_env()
    assert os.environ["VIRTUAL_ENV"] == "/caller/.venv"


def test_the_gh_release_lookup_argv_and_timeout(monkeypatch):
    """🔴 GUARD: `gh release view` — exact argv (tag, --repo slug, url query), 30s timeout."""
    seen = []

    def spy(cmd, **kw):
        seen.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "https://github.com/o/r/x\n", "")
    monkeypatch.setattr(release.subprocess, "run", spy)
    assert _REAL_GH_RELEASE_URL({"repo_slug": "o/r", "tag": "v0.2.0"}) == "https://github.com/o/r/x"
    (cmd, kw), = seen
    assert cmd == ["gh", "release", "view", "v0.2.0", "--repo", "o/r", "--json", "url",
                   "-q", ".url"]
    assert kw["timeout"] == 30 and kw["capture_output"] is True and kw["text"] is True


def test_git_runs_with_c_cwd_captured_text_and_the_default_timeout(monkeypatch, tmp_path):
    seen = _spy_run(monkeypatch)
    release._git(tmp_path, "status", "-s")
    release._git(tmp_path, "fetch", timeout=77)
    assert seen[0] == (["git", "-C", str(tmp_path), "status", "-s"],
                       {"capture_output": True, "text": True, "timeout": 120})
    assert seen[1][0] == ["git", "-C", str(tmp_path), "fetch"] and seen[1][1]["timeout"] == 77


@pytest.mark.parametrize("call,argv_tail,timeout", [
    (lambda d: release._fetch(d),
     ["fetch", "--quiet", "origin", "+refs/heads/main:refs/remotes/origin/main",
      "+refs/heads/dev:refs/remotes/origin/dev"], 300),
    (lambda d: release._remote_tag(d, "v0.2.0"),
     ["ls-remote", "--tags", "origin", "refs/tags/v0.2.0"], 60),
    (lambda d: release.remote_dev_head({"git_dir": str(d)}, max_age=0),
     ["ls-remote", "origin", "refs/heads/dev"], 60),
    (lambda d: release._rev(d, "HEAD"), ["rev-parse", "-q", "--verify", "HEAD^{commit}"], 120),
    (lambda d: release._is_ancestor(d, "a", "b"), ["merge-base", "--is-ancestor", "a", "b"], 120),
    (lambda d: release._repo_slug(d), ["remote", "get-url", "origin"], 120),
])
def test_each_git_seam_passes_its_exact_argv_and_timeout(monkeypatch, tmp_path, call,
                                                         argv_tail, timeout):
    """🔴 GUARD: every git call outside the `world` flow, on the SUCCESS path."""
    release._dev_head_cache.clear()
    seen = _spy_run(monkeypatch)
    call(tmp_path)
    (cmd, kw), = seen
    assert cmd == ["git", "-C", str(tmp_path), *argv_tail]
    assert kw["timeout"] == timeout and kw["capture_output"] is True and kw["text"] is True


def test_complete_pushes_main_tag_dev_in_order_plain_with_300s(world, monkeypatch):
    """🔴 GUARD: the three pushes' exact argv and timeout on the success path (no `+`,
    no --force)."""
    calls = []
    real = release._git

    def spy(cwd, *args, **kw):
        if args and args[0] == "push":
            calls.append((str(cwd), args, kw))
        return real(cwd, *args, **kw)
    monkeypatch.setattr(release, "_git", spy)
    out = _prepare(world)
    overrides.set_fields(out["request_id"], state=overrides.MERGING)
    res = release.complete(overrides.load(out["request_id"]), "terminal:op")
    assert res["ok"] is True
    sha, tag = res["merge_commit_sha"], release.tag_name("0.2.0")
    assert [c[1] for c in calls] == [
        ("push", "--quiet", "origin", f"{sha}:refs/heads/main"),
        ("push", "--quiet", "origin", f"refs/tags/{tag}:refs/tags/{tag}"),
        ("push", "--quiet", "origin", f"{sha}:refs/heads/dev")]
    assert all(c[2] == {"timeout": 300} for c in calls)
    assert len({c[0] for c in calls}) == 1


def test_a_uv_lock_recording_only_another_package_at_the_new_version_refuses(world,
                                                                             monkeypatch):
    """🔴 GUARD (deterministic): the lock changes, and ANOTHER package (click) sits at the
    new version, but THIS project (demo) does not ⇒ refuse. Own fixture, no shared state."""
    def lock_other(wt):
        (wt / "uv.lock").write_text(
            'version = 1\n\n[[package]]\nname = "click"\nversion = "0.2.0"\n\n'
            '[[package]]\nname = "demo"\nversion = "0.1.5"\nsource = { editable = "." }\n')
        return _ok()
    monkeypatch.setattr(release, "_uv_lock", lock_other)
    out = _prepare(world)
    assert out["ok"] is False and "version mismatch" in out["error"]
    assert "demo 0.2.0" in out["error"]
    _assert_refused_and_clean(world, out, why="version mismatch")


# --- CMX-87 round 11: every `except` clause tested with an exception its narrowing misses ---
#
# A `except Exception` narrowed to one type, or an `except OSError` narrowed to one of its
# subclasses, must turn a test RED: each test below raises a type the clause is MEANT to catch
# but which the narrower clause would not (RuntimeError for `except Exception`; PermissionError /
# IsADirectoryError — OSError subclasses — for `except OSError`).

import logging  # noqa: E402


class _Boom(RuntimeError):
    pass


# prepare(): a step that crashes with ANY exception refuses and leaves NOTHING behind -------

@pytest.mark.parametrize("exc", [RuntimeError("boom"), OSError("disk"), KeyError("k"),
                                 _Boom("custom")])
@pytest.mark.parametrize("seam", ["_uv_lock", "_release_tests"])
def test_a_release_step_that_crashes_with_any_exception_refuses_and_leaves_nothing(
        world, pushes, monkeypatch, caplog, seam, exc):
    def crash(*a, **k):
        raise exc
    monkeypatch.setattr(release, seam, crash)
    with caplog.at_level(logging.ERROR, logger=release.log.name):
        out = _prepare(world)
    _assert_refused_and_clean(world, out, why="internal error")
    assert pushes == []
    assert any("release steps crashed" in r.getMessage() for r in caplog.records)


def test_steps_itself_raising_a_runtime_error_refuses_and_leaves_nothing(world, monkeypatch):
    monkeypatch.setattr(release, "_steps", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("steps exploded")))
    _assert_refused_and_clean(world, _prepare(world), why="internal error: steps exploded")


# prepare(): a push notification that fails is best effort ---------------------------------

@pytest.mark.parametrize("exc", [RuntimeError("telegram down"), KeyError("x"), OSError("net")])
def test_a_failing_request_notification_never_fails_the_request(world, monkeypatch, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(release.notify, "enabled", lambda: True)
    monkeypatch.setattr(release.notify, "send", boom)
    out = _prepare(world)
    assert out["ok"] is True and out["pending"] is True
    rec = overrides.load(out["request_id"])
    assert rec is not None and rec["state"] == overrides.PENDING
    assert _local(world, "refs/tags/v0.2.0") is not None, "the release is kept"


def test_a_failing_notify_enabled_check_never_fails_the_request(world, monkeypatch):
    def boom():
        raise RuntimeError("config unreadable")
    monkeypatch.setattr(release.notify, "enabled", boom)
    assert _prepare(world)["ok"] is True


# overrides: a release cleanup that raises is best effort ----------------------------------

@pytest.mark.parametrize("exc", [RuntimeError("cleanup boom"), KeyError("git_dir"),
                                 OSError("busy")])
def test_a_release_cleanup_that_raises_never_changes_the_recorded_outcome(
        world, pushes, monkeypatch, caplog, exc):
    out = _prepare(world)

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(release, "cleanup", boom)
    with caplog.at_level(logging.WARNING, logger=overrides.log.name):
        res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["ok"] is True and res["state"] == overrides.RELEASED, res
    rec = overrides.load(out["request_id"])
    assert rec["state"] == overrides.RELEASED
    assert rec["pushed"] == ["main", "v0.2.0", "dev"]
    release_sha = rec["release_sha"]
    assert _remote(world, "refs/heads/main") == release_sha
    assert _remote(world, "refs/heads/dev") == release_sha
    assert _remote(world, "refs/tags/v0.2.0^{commit}") == release_sha
    assert any(r.levelno == logging.WARNING and "release cleanup failed" in r.getMessage()
               and out["request_id"] in r.getMessage() for r in caplog.records)


def test_a_release_cleanup_that_raises_after_a_deny_still_returns_denied(
        world, pushes, monkeypatch, caplog):
    out = _prepare(world)
    monkeypatch.setattr(release, "cleanup", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("cleanup boom")))
    with caplog.at_level(logging.WARNING, logger=overrides.log.name):
        res = overrides.decide(out["request_id"], False, "terminal:op")
    assert res["ok"] is True and res["state"] == overrides.DENIED
    assert overrides.load(out["request_id"])["state"] == overrides.DENIED
    assert any("release cleanup failed" in r.getMessage() for r in caplog.records)
    assert pushes == []


# overrides.decide: a completion that crashes with ANY exception fails closed, never wedges ---

@pytest.mark.parametrize("exc", [RuntimeError("complete boom"), KeyError("tag"),
                                 OSError("io")])
def test_a_release_completion_that_crashes_fails_the_request_and_cleans_up(
        world, pushes, told, monkeypatch, caplog, exc):
    out = _prepare(world)

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(release, "complete", boom)
    with caplog.at_level(logging.ERROR, logger=overrides.log.name):
        res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["ok"] is False and res["state"] == overrides.FAILED
    assert res["detail"].startswith("internal error: ")
    rec = overrides.load(out["request_id"])
    assert rec["state"] == overrides.FAILED and rec["detail"] == res["detail"]
    assert pushes == [] and _remote(world, "refs/heads/dev") != rec["release_sha"]
    assert _release_worktrees() == []
    assert _local(world, "refs/tags/v0.2.0") is None
    assert [k for k, *_ in told] == ["release_failed"]
    assert any("override completion crashed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("exc", [RuntimeError("merge boom"), KeyError("x")])
def test_an_override_completion_that_crashes_fails_the_request_never_raises(
        monkeypatch, told, exc):
    from chela import contract

    def boom(rec, by):
        raise exc
    monkeypatch.setattr(contract, "complete_override", boom)
    rec = _override()
    res = overrides.decide(rec["id"], True, "terminal:op")
    assert res["ok"] is False and res["state"] == overrides.FAILED
    assert res["detail"].startswith("internal error: ")
    assert overrides.load(rec["id"])["state"] == overrides.FAILED
    assert [k for k, *_ in told] == ["override_failed"]


# overrides._tell: each of the two channels is best effort AND independent -----------------

@pytest.mark.parametrize("kind", [None, "release"])
def test_a_failing_push_notification_does_not_stop_the_inbox_notice(monkeypatch, kind):
    seen = []
    monkeypatch.setattr(overrides.notify, "enabled", lambda: True)
    monkeypatch.setattr(overrides.notify, "send", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("push down")))
    monkeypatch.setattr(inbox, "enqueue", lambda k, s, p: seen.append(k) or True)
    rec = {"id": "override-" + "a" * 24, "label": "L", "kind": kind, "task_id": "CMX-9"}
    overrides._tell(rec, overrides.FAILED, "why")
    assert seen == [f"{'release' if kind else 'override'}_failed"]


@pytest.mark.parametrize("kind", [None, "release"])
def test_a_failing_inbox_enqueue_does_not_fail_the_telling(monkeypatch, kind):
    sent = []
    monkeypatch.setattr(overrides.notify, "enabled", lambda: True)
    monkeypatch.setattr(overrides.notify, "send", lambda text, title=None, **k: sent.append(
        title))
    monkeypatch.setattr(inbox, "enqueue", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("inbox down")))
    rec = {"id": "override-" + "a" * 24, "label": "L", "kind": kind, "task_id": "CMX-9"}
    overrides._tell(rec, overrides.FAILED, "why")           # must not raise
    assert len(sent) == 1


def test_a_failing_telling_never_changes_a_failed_approvals_outcome(
        world, pushes, monkeypatch):
    _reject_dev_pushes(world)
    out = _prepare(world)
    monkeypatch.setattr(overrides.notify, "enabled", lambda: True)
    monkeypatch.setattr(overrides.notify, "send", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("push down")))
    monkeypatch.setattr(inbox, "enqueue", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("inbox down")))
    res = overrides.decide(out["request_id"], True, "terminal:op")
    assert res["ok"] is False and res["state"] == overrides.FAILED
    assert overrides.load(out["request_id"])["state"] == overrides.FAILED


# overrides.sweep: a head lookup that raises is "unknown", never an expiry or a crash -------

@pytest.mark.parametrize("exc", [RuntimeError("ls-remote boom"), KeyError("x"), OSError("io")])
def test_a_sweep_whose_dev_lookup_raises_keeps_the_release_pending(world, pushes, exc):
    out = _prepare(world)

    def boom(rec):
        raise exc
    assert overrides.sweep(dev_head_of=boom) == []
    assert overrides.load(out["request_id"])["state"] == overrides.PENDING


@pytest.mark.parametrize("exc", [RuntimeError("db boom"), KeyError("x")])
def test_a_sweep_whose_pr_head_lookup_raises_keeps_the_override_pending(exc):
    rec = _override()

    def boom(task_id):
        raise exc
    assert overrides.sweep(head_of=boom) == []
    assert overrides.load(rec["id"])["state"] == overrides.PENDING


# overrides store: the OSError / ValueError clauses ----------------------------------------

def test_an_unwritable_store_is_false_not_an_exception(monkeypatch, caplog):
    rec = {"id": overrides.new_request_id(), "state": overrides.PENDING}
    real = Path.write_text

    def deny(self, *a, **k):
        if self.suffix == ".tmp":
            raise PermissionError("read-only")             # an OSError SUBCLASS
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", deny)
    with caplog.at_level(logging.WARNING, logger=overrides.log.name):
        assert overrides._write(rec) is False
        assert overrides.create({"head_sha": "a" * 40}) is None
    assert any("could not write" in r.getMessage() for r in caplog.records)
    assert overrides.all_requests() == []


def test_a_failed_mkdir_in_the_store_is_false_not_an_exception(monkeypatch):
    def deny(self, *a, **k):
        raise PermissionError("no")
    monkeypatch.setattr(Path, "mkdir", deny)
    assert overrides._write({"id": overrides.new_request_id()}) is False


@pytest.mark.parametrize("payload", [b"{not json", b"\xff\xfe\x00bad utf8", b""])
def test_an_unreadable_request_file_loads_as_none(payload):
    rid = overrides.new_request_id()
    overrides.store_dir().mkdir(parents=True, exist_ok=True)
    (overrides.store_dir() / f"{rid}.json").write_bytes(payload)   # JSON error / UnicodeDecodeError
    assert overrides.load(rid) is None
    assert overrides.all_requests() == []


def test_a_request_path_that_is_a_directory_loads_as_none():
    rid = overrides.new_request_id()
    (overrides.store_dir() / f"{rid}.json").mkdir(parents=True)    # IsADirectoryError
    assert overrides.load(rid) is None
    assert overrides.all_requests() == []


def test_an_unlistable_store_is_an_empty_list(monkeypatch):
    def deny(self, *a, **k):
        raise PermissionError("no")
    monkeypatch.setattr(Path, "glob", deny)
    assert overrides.all_requests() == []


# release.py: the OSError / TimeoutExpired seams, with an OSError SUBCLASS -----------------

@pytest.mark.parametrize("exc", [PermissionError("denied"), IsADirectoryError("dir")])
def test_every_release_subprocess_seam_treats_any_oserror_as_a_failed_step(
        monkeypatch, tmp_path, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(release.subprocess, "run", boom)
    for p in (release._git(tmp_path, "push"), _REAL_UV_LOCK(tmp_path),
              _REAL_RELEASE_TESTS(tmp_path)):
        assert p.returncode != 0 and str(exc) in p.stderr
    assert _REAL_GH_RELEASE_URL({"repo_slug": "o/r", "tag": "v1.0.0"}) is None


def test_an_oserror_subclass_from_release_notes_refuses_with_the_reason(world, monkeypatch):
    def boom(*a, **k):
        raise PermissionError("CHANGELOG.md locked")
    monkeypatch.setattr(release.release_notes, "promote_unreleased", boom)
    out = _prepare(world)
    assert out["ok"] is False and "release notes: CHANGELOG.md locked" in out["error"]
    assert _release_worktrees() == [] and overrides.all_requests() == []


def test_an_oserror_subclass_reading_uv_lock_refuses_with_the_reason(world, monkeypatch):
    def lock_becomes_dir(wt: Path) -> subprocess.CompletedProcess:
        (wt / "uv.lock").unlink()
        (wt / "uv.lock").mkdir()                            # read_text -> IsADirectoryError
        return _ok()
    monkeypatch.setattr(release, "_uv_lock", lock_becomes_dir)
    out = _prepare(world)
    assert out["ok"] is False and "uv.lock unreadable" in out["error"]
    assert _release_worktrees() == [] and overrides.all_requests() == []


# overridewatch.OverrideRelay.tick: each stage is best effort and independent ---------------

class _Bot:
    def __init__(self):
        self.posts = []

    def post(self, text, message_thread_id=None, reply_markup=None):
        self.posts.append(message_thread_id)
        return 100 + len(self.posts)

    def edit(self, *a, **k):
        return True


def _relay_for(bot, thread=lambda: "42"):
    from chela.telegram.overridewatch import OverrideRelay
    return OverrideRelay(post=bot.post, edit=bot.edit, thread=thread)


def test_a_sweep_that_raises_does_not_stop_the_relay(monkeypatch, caplog):
    _override()
    monkeypatch.setattr(overrides, "sweep", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("sweep boom")))
    bot = _Bot()
    with caplog.at_level(logging.ERROR):
        _relay_for(bot).tick()
    assert bot.posts == ["42"]
    assert any("override sweep failed" in r.getMessage() for r in caplog.records)


def test_a_release_url_poll_that_raises_does_not_stop_the_relay(monkeypatch, caplog):
    _override()
    monkeypatch.setattr(release, "poll_release_urls", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("gh boom")))
    bot = _Bot()
    with caplog.at_level(logging.ERROR):
        _relay_for(bot).tick()
    assert bot.posts == ["42"]
    assert any("release URL poll failed" in r.getMessage() for r in caplog.records)


def test_a_request_whose_sync_raises_does_not_stop_the_next_one(caplog):
    first, second = _override(), _override()
    calls = []

    class _Flaky(_Bot):
        def post(self, text, message_thread_id=None, reply_markup=None):
            calls.append(text)
            if len(calls) == 1:
                raise RuntimeError("telegram 500")
            return super().post(text, message_thread_id, reply_markup)
    bot = _Flaky()
    with caplog.at_level(logging.ERROR):
        _relay_for(bot).tick()
    assert len(calls) == 2 and len(bot.posts) == 1, "the second request still got its card"
    assert any("override relay failed" in r.getMessage() for r in caplog.records)
    assert first["id"] != second["id"]


def test_a_topic_lookup_that_raises_posts_to_the_general_topic():
    _override()

    def boom():
        raise RuntimeError("bindings unreadable")
    bot = _Bot()
    _relay_for(bot, thread=boom).tick()
    assert bot.posts == [None]


# main.py ----------------------------------------------------------------------------------

def test_the_bridges_override_loop_survives_a_relay_tick_that_raises():
    import threading

    from chela import main as chela_main
    stop = threading.Event()
    ticks = []

    class _Relay:
        def tick(self):
            ticks.append(1)
            if len(ticks) == 1:
                raise RuntimeError("tick boom")
            stop.set()
    t = threading.Thread(target=chela_main._override_loop, args=(_Relay(), 0, stop),
                         daemon=True)
    t.start()
    t.join(5)
    assert ticks == [1, 1], "a raising tick must not end the loop"


def test_a_body_file_that_is_a_directory_refuses_cleanly(world, tmp_path, capsys):
    from chela import main
    with pytest.raises(SystemExit) as exc:
        main.cmd_release(SimpleNamespace(version="0.2.0", theme="t", body_file=str(tmp_path),
                                         repo=str(world.work)))      # IsADirectoryError
    out = capsys.readouterr().out
    assert exc.value.code == 1 and "--body-file unreadable" in out
    assert overrides.all_requests() == [] and _local(world, "refs/tags/v0.2.0") is None


# contract.py: the override's merge mechanics (OSError subclasses) -------------------------

def _contract_run(monkeypatch, *, merge=None, view=None):
    from chela import contract
    calls = []

    def fake(argv, **k):
        calls.append(argv)
        step = merge if argv[:3] == ["gh", "pr", "merge"] else view
        if isinstance(step, BaseException):
            raise step
        return step
    monkeypatch.setattr(contract.subprocess, "run", fake)
    return contract, calls


_PR = "https://github.com/o/r/pull/9"
_RUN = {"task_id": "CMX-9"}


def test_a_squash_merge_whose_gh_cannot_run_reports_it_for_any_oserror(monkeypatch):
    contract, _ = _contract_run(monkeypatch, merge=PermissionError("gh: denied"))
    out = contract._squash_merge(_RUN, "/tmp", _PR, match_head="a" * 40)
    assert out["ok"] is False and "gh CLI could not be run: gh: denied" in out["error"]


def test_a_squash_merge_that_times_out_says_so(monkeypatch):
    contract, _ = _contract_run(monkeypatch, merge=subprocess.TimeoutExpired(["gh"], 1))
    out = contract._squash_merge(_RUN, "/tmp", _PR)
    assert out == {"ok": False, "error": "gh pr merge timed out"}


@pytest.mark.parametrize("exc", [PermissionError("no"), subprocess.TimeoutExpired(["gh"], 15)])
def test_a_merge_that_landed_stays_ok_when_its_sha_cannot_be_read(monkeypatch, exc):
    contract, _ = _contract_run(
        monkeypatch, merge=subprocess.CompletedProcess([], 0, "", ""), view=exc)
    out = contract._squash_merge(_RUN, "/tmp", _PR)
    assert out == {"ok": True, "merge_commit_sha": None}


@pytest.mark.parametrize("exc", [PermissionError("no"), subprocess.TimeoutExpired(["git"], 30)])
def test_a_post_merge_cleanup_step_that_cannot_run_is_logged_never_raised(
        monkeypatch, caplog, exc):
    contract, _ = _contract_run(monkeypatch)
    monkeypatch.setattr(contract.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(exc))
    with caplog.at_level(logging.WARNING, logger=contract.log.name):
        contract._best_effort("CMX-9", "branch-delete", ["git", "branch", "-D", "b"], "/tmp", 5)
    assert any("merge cleanup branch-delete failed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("exc", [PermissionError("no"), subprocess.TimeoutExpired(["gh"], 15)])
def test_an_unreadable_pr_base_is_none_for_any_oserror(monkeypatch, exc):
    contract, _ = _contract_run(monkeypatch, view=exc)
    monkeypatch.setattr(contract.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(exc))
    assert contract._read_pr_base(_PR, "/tmp") is None


@pytest.mark.parametrize("stdout", ["not json", "[1]", '{"baseRefName": ""}', ""])
def test_a_garbled_pr_base_reply_is_none(monkeypatch, stdout):
    from chela import contract
    monkeypatch.setattr(contract.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, stdout, ""))
    assert contract._read_pr_base(_PR, "/tmp") is None


def test_an_unresolvable_repo_dir_is_not_the_control_repo(monkeypatch):
    from chela import contract

    def boom(self, *a, **k):
        raise PermissionError("no")
    monkeypatch.setattr(Path, "resolve", boom)
    assert contract._is_control_repo("/tmp") is False
