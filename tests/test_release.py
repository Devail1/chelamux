"""🏷️ CMX-87 — `chela release X.Y.Z`: the orchestrator PREPARES a release (commit + tag
LOCALLY, nothing pushed) and requests approval; the operator's approval pushes main, the tag,
then dev — fast-forward only, once. dev moving, a deny, a failed step ⇒ nothing pushed.

No live GitHub: `origin` is a local bare repository. The two `uv` steps are seams (`_uv_lock`
and `_release_tests`) faked per test; every git command is real. The request store is the
real override store under the sandboxed `CHELA_DIR` (conftest). No live tmux or Telegram.
"""
from __future__ import annotations

import re
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
