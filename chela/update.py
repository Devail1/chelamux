"""``chela update`` — the human-run half of self-update. Part 1 of 2.

Adopters otherwise have to know the manual dance: ``git pull`` the checkout, ``uv sync``
its deps, ``pm2 restart`` the services onto the new code. This module gives that dance a
name and a safety rail, plus a heads-up (:func:`check_and_notify`, wired into the daemon
loop in ``cmd_run``) when the checkout has fallen behind its upstream.

⛔ **This is deliberately NOT automatic.** :func:`apply` only ever runs when a human (or a
script a human runs) calls ``chela update``. There is no flag here that pulls code on its
own — that is part 2 (an off-by-default ``CHELA_AUTO_UPDATE`` sweep), a separate, larger
trust decision this module does not make for you.

The safety rail that makes ``apply()`` a command you can run without reading its diff
first: it refuses outright on a dirty working tree (never clobbers local edits) and on a
diverged branch (never forces a merge past commits only the local checkout has) — both
checked BEFORE anything touches disk. Only then does it pull, re-sync dependencies with
every extra installed (never ``--frozen``, which prunes extras nobody asked to remove),
and restart whatever ``chela-*`` PM2 services are actually running.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from chela import notify
from chela.dispatcher import GIT_TIMEOUT_SECONDS, _git, _git_ok, _git_out

log = logging.getLogger(__name__)

# Non-git subprocesses (uv, pm2) get more slack than a git round-trip: a dependency
# re-sync or a service restart can legitimately take longer than GIT_TIMEOUT_SECONDS.
_SHELL_TIMEOUT_SECONDS = 300


class NotAGitCheckout(RuntimeError):
    """Raised by :func:`repo_root` when chela was installed some other way (e.g. pip)."""


@dataclass(frozen=True)
class UpdateStatus:
    """What :func:`commits_behind` found — or why it couldn't tell."""

    ok: bool
    behind: int = 0
    ahead: int = 0
    branch: str = ""
    error: str = ""


@dataclass(frozen=True)
class ApplyResult:
    """What :func:`apply` did, or the step it refused / failed at."""

    ok: bool
    step: str = ""              # "dirty-check" | "fetch" | "ff-check" | "pull" | "uv-sync" | "pm2-restart" | "done"
    error: str = ""
    behind_before: int = 0
    restarted: list[str] = field(default_factory=list)


def repo_root() -> Path:
    """The git checkout chela's own code lives in — NOT :data:`chela.config.CHELA_DIR`,
    which is chela's *state* directory and exists even for a pip install.

    Derived from this file's own location rather than the caller's cwd, since ``chela
    update`` must work no matter what directory it's invoked from.
    """
    root = Path(__file__).resolve().parents[1]
    if not (root / ".git").exists():
        raise NotAGitCheckout(
            f"{root} is not a git checkout — chela was likely installed via pip, not "
            "`git clone`; update it with `pip install --upgrade chelamux` instead"
        )
    return root


def _sh(args: list[str], cwd: Path, timeout: float = _SHELL_TIMEOUT_SECONDS):
    """Run a non-git subprocess. Returns None if the binary is missing or it hangs —
    the same never-raise contract as ``dispatcher._git``, for the same reason: a missing
    `uv`/`pm2` must fail the update step legibly, not take the CLI down with a traceback.
    """
    try:
        return subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("%s failed in %s: %s", " ".join(args), cwd, e)
        return None


def _is_dirty(repo: Path) -> bool:
    cp = _git(repo, "status", "--porcelain")
    return bool(_git_out(cp)) if _git_ok(cp) else True  # can't tell -> assume dirty, refuse


def commits_behind(repo: Path | None = None, *, fetch: bool = True) -> UpdateStatus:
    """How far the checkout is from its upstream.

    ``fetch=True`` (the default — used by ``--check``, ``apply()``, and the daemon's
    periodic notifier) runs ``git fetch`` first, so the answer reflects what's really on
    the remote. ``fetch=False`` (used by the ``update_available`` capability row, which
    must stay fast and offline-safe for every ``chela doctor`` / daemon-boot call) reads
    only the LOCAL remote-tracking ref, so it's exactly as fresh as the last real fetch —
    never live, but never a network call either.
    """
    repo = repo or repo_root()
    if fetch:
        fetch_cp = _git(repo, "fetch", timeout=GIT_TIMEOUT_SECONDS)
        if not _git_ok(fetch_cp):
            err = fetch_cp.stderr.strip() if fetch_cp is not None else "git fetch failed to run"
            return UpdateStatus(ok=False, error=f"git fetch failed: {err}")

    branch = _git_out(_git(repo, "rev-parse", "--abbrev-ref", "HEAD"))
    upstream_cp = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not _git_ok(upstream_cp):
        return UpdateStatus(ok=True, behind=0, ahead=0, branch=branch,
                             error="no upstream configured for this branch")

    behind_cp = _git(repo, "rev-list", "--count", "HEAD..@{u}")
    ahead_cp = _git(repo, "rev-list", "--count", "@{u}..HEAD")
    if not _git_ok(behind_cp) or not _git_ok(ahead_cp):
        return UpdateStatus(ok=False, branch=branch, error="git rev-list failed")

    return UpdateStatus(
        ok=True,
        behind=int(_git_out(behind_cp) or "0"),
        ahead=int(_git_out(ahead_cp) or "0"),
        branch=branch,
    )


def _running_pm2_services(repo: Path) -> list[str]:
    """Every currently-online PM2 process named ``chela-*``. Empty (never an error) if
    PM2 isn't installed or nothing is running — a dev checkout run by hand has no
    services to restart, and that's a normal, not exceptional, outcome.
    """
    cp = _sh(["pm2", "jlist"], cwd=repo)
    if cp is None or cp.returncode != 0 or not cp.stdout.strip():
        return []
    try:
        procs = json.loads(cp.stdout)
    except ValueError:
        return []
    return sorted(
        p.get("name", "") for p in procs
        if isinstance(p, dict)
        and str(p.get("name", "")).startswith("chela-")
        and (p.get("pm2_env") or {}).get("status") == "online"
    )


def apply(repo: Path | None = None) -> ApplyResult:
    """The safe update sequence. Refuses before touching anything on a dirty or
    diverged tree; otherwise pulls, re-syncs, and restarts — in that order, every time.
    """
    repo = repo or repo_root()

    if _is_dirty(repo):
        return ApplyResult(ok=False, step="dirty-check",
                            error="working tree has uncommitted changes — refusing to "
                                  "update over local edits")

    status = commits_behind(repo, fetch=True)
    if not status.ok:
        return ApplyResult(ok=False, step="fetch", error=status.error)

    if status.ahead > 0:
        return ApplyResult(
            ok=False, step="ff-check", behind_before=status.behind,
            error=f"local branch is {status.ahead} commit(s) ahead of its upstream — "
                  "diverged, not fast-forwardable; resolve by hand",
        )

    if status.behind == 0:
        return ApplyResult(ok=True, step="done", behind_before=0)

    pull_cp = _git(repo, "pull", "--ff-only")
    if not _git_ok(pull_cp):
        err = pull_cp.stderr.strip() if pull_cp is not None else "git pull failed to run"
        return ApplyResult(ok=False, step="pull", behind_before=status.behind, error=err)

    sync_cp = _sh(["uv", "sync", "--all-extras"], cwd=repo)
    if sync_cp is None or sync_cp.returncode != 0:
        err = sync_cp.stderr.strip() if sync_cp is not None else "uv sync failed to run"
        return ApplyResult(ok=False, step="uv-sync", behind_before=status.behind, error=err)

    services = _running_pm2_services(repo)
    if services:
        restart_cp = _sh(["pm2", "restart", *services], cwd=repo)
        if restart_cp is None or restart_cp.returncode != 0:
            err = restart_cp.stderr.strip() if restart_cp is not None else "pm2 restart failed to run"
            return ApplyResult(ok=False, step="pm2-restart", behind_before=status.behind,
                                error=err, restarted=[])

    return ApplyResult(ok=True, step="done", behind_before=status.behind, restarted=services)


def check_and_notify(previously_behind: int) -> int:
    """Called from the daemon loop on a bounded cadence. Edge-triggered exactly like
    ``notify.check_waiting``: logs (and, if configured, pushes) a heads-up once on the
    transition into "behind", not once per tick — a drumbeat of the same line is how an
    operator learns to ignore the log. Returns the behind-count to remember as the next
    call's ``previously_behind``.

    ⛔ NEVER calls :func:`apply` / ``git pull`` — informing is this function's entire job.
    """
    status = commits_behind(fetch=True)
    if not status.ok:
        return previously_behind  # can't tell right now; don't flap the edge on a blip
    if status.behind > 0 and previously_behind == 0:
        log.warning("update available: %d commit(s) behind — run `chela update`", status.behind)
        if notify.enabled():
            notify.send(f"{status.behind} commit(s) behind — run `chela update`",
                         title="chela: update available")
    return status.behind
