"""🏷️ ``chela release X.Y.Z`` — the orchestrator PREPARES a release, the operator approves it
with one tap, and chela pushes ``main`` + the tag + ``dev`` (CMX-87).

Before this, a release was CONTRIBUTING.md's "Releasing" steps by hand. The orchestrator
could do the preparation in a worktree, but the merge-gate hook (correctly) denies an agent's
push to ``main``/``dev``, so the operator ran three pushes from a script. Now the two halves
are split the same way an override is (:mod:`chela.overrides`, CMX-79):

* :func:`prepare` — the requesting half, safe for an agent. In its OWN worktree off
  ``origin/dev`` (refused unless ``origin/main`` is an ancestor of it) it runs the release
  steps: :func:`chela.release_notes.promote_unreleased` + fragment deletion, the
  ``pyproject.toml`` bump, ``uv lock`` (which must change ``uv.lock`` to name the new
  version), and the release tests. Then it commits and creates the annotated tag LOCALLY,
  records ONE approval request in the override store (``kind: "release"``, bound to the
  ``dev`` commit it was cut from) and RETURNS. It pushes nothing. Any failed or dirty step
  refuses with the reason and leaves nothing behind.
* :func:`complete` — the approved half, run by :func:`chela.overrides.decide` in whichever
  process the operator approved from (the Telegram bridge, the dashboard, or
  ``chela merge-approve`` in a plain terminal). It re-checks, live, that ``origin/dev`` is
  still the commit the release was cut from and ``origin/main`` an ancestor of the release
  commit, then pushes ``release:main``, the tag, ``release:dev`` — in that order, each a
  plain (never forced) push, so a non-fast-forward is refused by git itself. Any failed
  check ⇒ nothing pushed.

``dev`` moving after the request expires it (:func:`chela.overrides.sweep` →
:func:`remote_dev_head`), exactly as a new push expires an override. Whatever the outcome,
the release's worktree and local branch are removed (:func:`cleanup`); the local tag is
kept only once it was pushed.

⛔ Approval stays human-only — the same guards as an override: :func:`chela.overrides.decide`
refuses an agent's process, and the merge-gate hook denies a session's ``merge-approve``,
its ``python`` call into ``chela.overrides.decide`` and into this module's ``complete``.
:func:`complete` itself acts only on a request whose stored state says it was approved.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import subprocess
import time
from datetime import date as _date
from pathlib import Path

from chela import config, event_log, notify, overrides, release_notes

log = logging.getLogger("chela.release")

KIND = "release"
BRANCH_PREFIX = "chela-release/"
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_PYPROJECT_VERSION = re.compile(r'(?m)^version = "([^"]+)"$')
_PYPROJECT_NAME = re.compile(r'(?m)^name = "([^"]+)"$')
_TASK_ID = re.compile(r"\bCMX-(\d+)\b", re.IGNORECASE)
# The release's own steps, verified in its worktree before anything is committed.
RELEASE_TESTS = ("tests/test_version.py", "tests/test_release_notes.py")
# What the release commit may touch — anything else in the worktree is a dirty step.
RELEASE_PATHS = ("CHANGELOG.md", "pyproject.toml", "uv.lock", "changelog.d")
# `remote_dev_head` is called from every sweep (the daemon tick, the bridge's relay, the
# dashboard): one `git ls-remote` per repo per this many seconds is plenty.
DEV_HEAD_CACHE_S = 60.0
_dev_head_cache: dict[str, tuple[float, str | None]] = {}
# How long after a release the bridge keeps asking GitHub for the Release page's URL.
RELEASE_URL_POLL_S = 3 * 3600.0
RELEASE_URL_EVERY_S = 60.0


def is_release(rec: dict | None) -> bool:
    return bool(rec) and rec.get("kind") == KIND


def tag_name(version: str) -> str:
    return f"v{version}"


# --- seams (tests replace these; never a live GitHub) -------------------------------------

def _git(cwd: str | Path, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    """``git -C <cwd> <args>`` — never raises; a timeout reads as a failure."""
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(["git", *args], 1, "", str(e))


def _uv_env() -> dict:
    env = dict(os.environ)
    # The release worktree gets its OWN environment, never the caller's active venv.
    env.pop("VIRTUAL_ENV", None)
    return env


def _uv_lock(cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["uv", "lock"], cwd=cwd, capture_output=True, text=True,
                              timeout=600, env=_uv_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(["uv", "lock"], 1, "", str(e))


def _release_tests(cwd: Path) -> subprocess.CompletedProcess:
    # `--extra dev`: pytest lives there — without it the release venv has no pytest and PATH
    # falls through to the CALLER's, which imports the caller's chela, not the release's.
    # `python -m pytest` pins the interpreter to the release worktree's own venv.
    # `-p no:cacheprovider`: a `.pytest_cache/` would make the worktree dirty.
    cmd = ["uv", "run", "--frozen", "--extra", "dev", "python", "-m", "pytest", "-q",
           "-p", "no:cacheprovider", *RELEASE_TESTS]
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900,
                              env=_uv_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def _gh_release_url(rec: dict) -> str | None:
    """The GitHub Release's URL once the Release workflow created it — else None."""
    slug = rec.get("repo_slug")
    if not slug:
        return None
    try:
        p = subprocess.run(["gh", "release", "view", rec.get("tag") or "", "--repo", slug,
                            "--json", "url", "-q", ".url"], capture_output=True, text=True,
                           timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    url = (p.stdout or "").strip()
    return url if p.returncode == 0 and url.startswith("https://") else None


# --- helpers ------------------------------------------------------------------------------

def _tail(p: subprocess.CompletedProcess, n: int = 600) -> str:
    out = ((p.stderr or "") + "\n" + (p.stdout or "")).strip()
    return out[-n:] if out else f"exit {p.returncode}"


def _rev(cwd, ref: str) -> str | None:
    p = _git(cwd, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}")
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def _is_ancestor(cwd, older: str, newer: str) -> bool:
    return _git(cwd, "merge-base", "--is-ancestor", older, newer).returncode == 0


def _fetch(cwd) -> str | None:
    """Refresh ``origin/main`` and ``origin/dev``; the error text, or None on success."""
    p = _git(cwd, "fetch", "--quiet", "origin",
             "+refs/heads/main:refs/remotes/origin/main",
             "+refs/heads/dev:refs/remotes/origin/dev", timeout=300)
    return None if p.returncode == 0 else f"`git fetch origin` failed: {_tail(p)}"


def _remote_tag(cwd, tag: str) -> bool | None:
    p = _git(cwd, "ls-remote", "--tags", "origin", f"refs/tags/{tag}", timeout=60)
    if p.returncode != 0:
        return None
    return bool(p.stdout.strip())


def _repo_slug(cwd) -> str | None:
    url = _git(cwd, "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


def _version_tuple(v: str) -> tuple[int, ...] | None:
    m = _VERSION_RE.match(v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def task_ids(section: str) -> list[str]:
    """``CMX-N`` ids in a CHANGELOG section, first appearance order, no duplicates."""
    seen: list[str] = []
    for m in _TASK_ID.finditer(section or ""):
        tid = f"CMX-{m.group(1)}"
        if tid not in seen:
            seen.append(tid)
    return seen


def _refuse(why: str, **extra) -> dict:
    return {"ok": False, "error": why, **extra}


def _git_common_dir(repo: Path) -> Path | None:
    p = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None


def cleanup(rec: dict, *, keep_tag: bool = False) -> None:
    """Remove the release's worktree and local branch — and its local tag unless it was
    pushed. Best effort, idempotent."""
    git_dir = rec.get("git_dir")
    if not git_dir or not Path(git_dir).exists():
        return
    wt = rec.get("worktree")
    if wt:
        _git(git_dir, "worktree", "remove", "--force", wt)
    _git(git_dir, "worktree", "prune")
    if rec.get("branch"):
        _git(git_dir, "branch", "-D", rec["branch"])
    if not keep_tag and rec.get("tag"):
        # Only OUR tag: never delete a same-named tag that points somewhere else.
        if _rev(git_dir, f"refs/tags/{rec['tag']}") == rec.get("release_sha"):
            _git(git_dir, "tag", "-d", rec["tag"])


# --- the requesting half ------------------------------------------------------------------

def _steps(wt: Path, version: str, theme: str, body: str) -> str | dict:
    """Run the release steps in ``wt``; the error text on any failure, else a dict with the
    new CHANGELOG section's task ids."""
    pyproject = wt / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    found = _PYPROJECT_VERSION.findall(text)
    if len(found) != 1:
        return "pyproject.toml must declare exactly one `version = \"…\"` line"
    current = found[0]
    if _version_tuple(current) is None or _version_tuple(version) <= _version_tuple(current):
        return f"{version} is not newer than the current version {current}"
    name = (_PYPROJECT_NAME.findall(text) or [None])[0]

    changelog, changelog_d = wt / "CHANGELOG.md", wt / "changelog.d"
    try:
        promoted = release_notes.promote_unreleased(
            changelog.read_text(encoding="utf-8"), version, _date.today().isoformat(),
            changelog_d)
        section = release_notes.extract_release_notes(promoted, version)
    except (OSError, ValueError) as e:
        return f"release notes: {e}"
    if not section.strip():
        return "nothing to release — `## [Unreleased]` and changelog.d/ are both empty"
    changelog.write_text(promoted, encoding="utf-8")
    for fragment in release_notes._fragment_paths(changelog_d):
        fragment.unlink()

    pyproject.write_text(_PYPROJECT_VERSION.sub(f'version = "{version}"', text, count=1),
                         encoding="utf-8")

    lock = _uv_lock(wt)
    if lock.returncode != 0:
        return f"`uv lock` failed: {_tail(lock)}"
    if _git(wt, "diff", "--quiet", "--", "uv.lock").returncode == 0:
        return ("`uv lock` changed nothing — uv.lock still names the previous version, so "
                "every later `uv run` would dirty the tree")
    try:
        locked = (wt / "uv.lock").read_text(encoding="utf-8")
    except OSError as e:
        return f"uv.lock unreadable: {e}"
    if name and not re.search(rf'(?m)^name = "{re.escape(name)}"\nversion = "{re.escape(version)}"$',
                              locked):
        return f"version mismatch — uv.lock does not record {name} {version}"

    tests = _release_tests(wt)
    if tests.returncode != 0:
        return f"release tests failed: {_tail(tests)}"

    add = _git(wt, "add", "-A", "--", *RELEASE_PATHS)
    if add.returncode != 0:
        return f"`git add` failed: {_tail(add)}"
    status = _git(wt, "status", "--porcelain", "--untracked-files=all")
    dirty = [ln for ln in status.stdout.splitlines() if ln[:1] in (" ", "?") or ln[1:2] != " "]
    if status.returncode != 0 or dirty:
        return "the release worktree is dirty after the steps: " + "; ".join(dirty[:10])
    msg = f"Release {version} — {theme}" + (f"\n\n{body.strip()}\n" if body.strip() else "")
    commit = _git(wt, "commit", "--quiet", "-m", msg)
    if commit.returncode != 0:
        return f"`git commit` failed: {_tail(commit)}"
    tag = _git(wt, "tag", "-a", tag_name(version), "-m", f"{version} — {theme}")
    if tag.returncode != 0:
        return f"`git tag` failed: {_tail(tag)}"
    return {"task_ids": task_ids(section)}


def prepare(version: str, theme: str, *, body: str = "", repo: str | Path | None = None,
            actor: str | None = None) -> dict:
    """Prepare release ``version`` LOCALLY and request the operator's approval — then RETURN.

    ``{"ok": True, "pending": True, "request_id", "version", "tag", "dev_sha",
    "release_sha", "task_ids", …}`` or ``{"ok": False, "error"}``. Pushes nothing; on a
    refusal, nothing it made is left behind (worktree, branch, tag).
    """
    version = (version or "").strip()
    version = version[1:] if version.startswith("v") else version
    theme = " ".join((theme or "").split())
    if _version_tuple(version) is None:
        return _refuse(f"{version!r} is not a release version (X.Y.Z)")
    if not theme:
        return _refuse("a release needs a --theme (one line; it titles the commit and tag)")
    for rec in overrides.pending():
        if is_release(rec):
            return _refuse(f"release {rec.get('tag')} is already waiting for approval "
                           f"({rec['id']}) — decide it first")

    git_dir = _git_common_dir(Path(repo) if repo else Path.cwd())
    if git_dir is None:
        return _refuse("not inside a git repository")
    err = _fetch(git_dir)
    if err:
        return _refuse(err)
    dev_sha = _rev(git_dir, "refs/remotes/origin/dev")
    main_sha = _rev(git_dir, "refs/remotes/origin/main")
    if not dev_sha or not main_sha:
        return _refuse("origin has no `main` and `dev` branches to release from")
    if not _is_ancestor(git_dir, main_sha, dev_sha):
        return _refuse(f"origin/main ({main_sha[:12]}) is not an ancestor of origin/dev "
                       f"({dev_sha[:12]}) — main would not fast-forward to a release cut "
                       "from dev")
    tag = tag_name(version)
    if _rev(git_dir, f"refs/tags/{tag}"):
        return _refuse(f"tag {tag} already exists locally")
    remote = _remote_tag(git_dir, tag)
    if remote is None:
        return _refuse(f"could not read origin's tags to check {tag}")
    if remote:
        return _refuse(f"tag {tag} already exists on origin")
    branch = f"{BRANCH_PREFIX}{tag}"
    if _rev(git_dir, f"refs/heads/{branch}"):
        return _refuse(f"local branch {branch} already exists (a previous release attempt?) "
                       f"— delete it first: git branch -D {branch}")

    wt = config.CHELA_DIR / "releases" / f"{tag}-{secrets.token_hex(3)}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    add = _git(git_dir, "worktree", "add", "--quiet", "-b", branch, str(wt), dev_sha)
    if add.returncode != 0:
        return _refuse(f"`git worktree add` failed: {_tail(add)}")
    meta = {"kind": KIND, "version": version, "tag": tag, "theme": theme,
            "git_dir": str(git_dir), "worktree": str(wt), "branch": branch,
            "head_sha": dev_sha, "dev_sha": dev_sha, "main_sha": main_sha}
    try:
        out = _steps(wt, version, theme, body or "")
    except Exception as e:                                   # noqa: BLE001 — never leave junk
        log.exception("release steps crashed")
        out = f"internal error: {e}"
    if isinstance(out, str):
        cleanup({**meta, "release_sha": _rev(wt, "HEAD")})
        return _refuse(f"release {version} refused: {out}")
    release_sha = _rev(wt, "HEAD")
    slug = _repo_slug(git_dir)
    ids = out["task_ids"]
    question = (f"Release {tag} — {theme}? Cut from dev {dev_sha[:12]}; "
                f"{len(ids)} task(s): {', '.join(ids) or 'none'}")
    meta.update(release_sha=release_sha, task_ids=ids, repo_slug=slug,
                label=f"release {tag}", title=theme, actor=actor or "human",
                question=question)
    rec = overrides.create(meta)
    if rec is None:
        cleanup(meta)
        return _refuse("the release request could not be recorded (CHELA_OVERRIDE_WAIT_S is "
                       "0, or chela's overrides directory is not writable) — nothing kept")
    event_log.append("orchestrator.release_requested", f"release {tag} requested: {theme}",
                     payload={k: v for k, v in rec.items() if k != "telegram"})
    hint = approve_hint(rec["id"])
    try:
        if notify.enabled():
            notify.send(f"{question}\n\n{hint}", title=f"chela: approve release {tag}?")
    except Exception:                                        # noqa: BLE001 — best effort
        log.debug("release notify failed", exc_info=True)
    return {"ok": True, "pending": True, "request_id": rec["id"], "version": version,
            "tag": tag, "theme": theme, "dev_sha": dev_sha, "main_sha": main_sha,
            "release_sha": release_sha, "task_ids": ids, "question": question,
            "hint": hint, "ttl": rec["ttl"]}


def approve_hint(request_id: str) -> str:
    return (f"Approve with the Telegram button, on the dashboard (/override/{request_id}), "
            f"or in a plain terminal: chela merge-approve {request_id}  (deny: add --deny)")


# --- the approved half --------------------------------------------------------------------

def complete(rec: dict, approved_by: str) -> dict:
    """Push the approved release — or say why not. ``{ok, state, detail, merge_commit_sha?,
    pushed}``; ``state`` is released / expired (dev moved) / failed.

    Called by :func:`chela.overrides.decide` after it moved the request to ``merging``. It
    re-reads that from disk and refuses anything else: no push without a recorded approval.
    """
    stored = overrides.load(rec.get("id") or "")
    if not stored or stored.get("state") != overrides.MERGING or not is_release(stored):
        return {"ok": False, "state": overrides.FAILED, "pushed": [],
                "detail": "this release request was not approved — nothing pushed"}
    git_dir, tag = stored.get("git_dir"), stored.get("tag")
    dev_sha, release_sha = stored.get("dev_sha"), stored.get("release_sha")

    def fail(why: str, state: str = overrides.FAILED, pushed=()) -> dict:
        return {"ok": False, "state": state, "detail": why, "pushed": list(pushed)}

    if not (git_dir and Path(git_dir).exists() and tag and dev_sha and release_sha):
        return fail("the release's repository or commit is gone — nothing pushed")
    err = _fetch(git_dir)
    if err:
        return fail(f"{err} — nothing pushed")
    live_dev = _rev(git_dir, "refs/remotes/origin/dev")
    live_main = _rev(git_dir, "refs/remotes/origin/main")
    if live_dev != dev_sha:
        return fail(f"dev moved since the release was cut ({dev_sha[:12]} → "
                    f"{(live_dev or '?')[:12]}) — nothing pushed; cut the release again",
                    state=overrides.EXPIRED)
    if not live_main or not _is_ancestor(git_dir, live_main, release_sha):
        return fail(f"origin/main ({(live_main or '?')[:12]}) is not an ancestor of the "
                    "release commit — main would not fast-forward; nothing pushed")
    if _rev(git_dir, f"refs/tags/{tag}") != release_sha:
        return fail(f"the local tag {tag} no longer points at the release commit — "
                    "nothing pushed")
    if _rev(git_dir, f"{release_sha}^") != dev_sha:
        return fail("the release commit is not a single commit on top of dev — nothing "
                    "pushed")
    remote = _remote_tag(git_dir, tag)
    if remote is None or remote:
        return fail(f"tag {tag} {'already exists on origin' if remote else 'could not be checked on origin'}"
                    " — nothing pushed")

    pushed: list[str] = []
    for what, refspec in (("main", f"{release_sha}:refs/heads/main"),
                          (tag, f"refs/tags/{tag}:refs/tags/{tag}"),
                          ("dev", f"{release_sha}:refs/heads/dev")):
        # A plain push — never `+`/--force — so git refuses anything but a fast-forward.
        p = _git(git_dir, "push", "--quiet", "origin", refspec, timeout=300)
        if p.returncode != 0:
            done = ", ".join(pushed) or "nothing"
            return fail(f"pushing {what} failed ({_tail(p, 300)}) — pushed so far: {done}",
                        pushed=pushed)
        pushed.append(what)
    event_log.append("orchestrator.release_pushed",
                     f"release {tag} APPROVED by {approved_by} and pushed ({release_sha[:12]})",
                     payload={"request_id": stored["id"], "tag": tag, "version":
                              stored.get("version"), "release_sha": release_sha,
                              "dev_sha": dev_sha, "approved_by": approved_by,
                              "task_ids": stored.get("task_ids")})
    return {"ok": True, "state": overrides.RELEASED, "detail": None,
            "merge_commit_sha": release_sha, "pushed": pushed}


# --- the sweep's and the relay's reads ----------------------------------------------------

def remote_dev_head(rec: dict, *, max_age: float = DEV_HEAD_CACHE_S) -> str | None:
    """origin's ``dev`` head for a release request (``git ls-remote``, cached ``max_age``
    seconds per repository). None when it cannot be read — never a guess."""
    git_dir = rec.get("git_dir")
    if not git_dir or not Path(git_dir).exists():
        return None
    now = time.monotonic()
    hit = _dev_head_cache.get(git_dir)
    if hit and now - hit[0] < max_age:
        return hit[1]
    p = _git(git_dir, "ls-remote", "origin", "refs/heads/dev", timeout=60)
    sha = p.stdout.split()[0] if p.returncode == 0 and p.stdout.split() else None
    _dev_head_cache[git_dir] = (now, sha)
    return sha


def poll_release_urls(now: float | None = None) -> None:
    """Record each recent release's GitHub Release URL once the Release workflow created it,
    so the Telegram card can link it. Throttled per request; best effort."""
    now = time.time() if now is None else now
    for rec in overrides.all_requests():
        if not is_release(rec) or rec.get("state") != overrides.RELEASED:
            continue
        if rec.get("release_url") or now - (rec.get("finished_at") or 0) > RELEASE_URL_POLL_S:
            continue
        if now - (rec.get("release_url_checked") or 0) < RELEASE_URL_EVERY_S:
            continue
        url = _gh_release_url(rec)
        overrides.set_fields(rec["id"], release_url_checked=now,
                             **({"release_url": url} if url else {}))
