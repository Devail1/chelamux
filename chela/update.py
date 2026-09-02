"""``chela update`` — self-update, in two halves.

Adopters otherwise have to know the manual dance: ``git pull`` the checkout, ``uv sync``
its deps, ``pm2 restart`` the services onto the new code — AND refresh the plugin every
agent loads its hooks from, which is a *separate* copy Claude Code made at install time
(see :mod:`chela.hooks`) and which the server-side dance above never touches. This module
gives the whole thing a name and a safety rail, plus a heads-up (:func:`check_and_notify`,
wired into the daemon loop in ``cmd_run``) when the checkout has fallen behind its
upstream.

**The plugin half is not a human's job either, and it does not wait for a pull.** ``claude
plugin marketplace update <marketplace>`` and ``claude plugin update <plugin>@<marketplace>``
are both fully non-interactive — :func:`_refresh_plugin_if_needed` runs them, on EVERY
``apply()`` outcome including the already-up-to-date one, whenever an installed copy of
chela's plugin (:func:`chela.hooks.installed_plugins`) is drifted or unreadable. A stale
installed copy is not a cosmetic gap and is not always caused by a commit landing: the
motivating incident was a plugin-in-use sweep that deleted the cache directory
``installed_plugins.json`` still pointed at, on a checkout with nothing to pull — every
window started after that loaded NO hooks at all, silently killing outbound Telegram relay
until someone noticed and re-ran the two CLI calls by hand. Gated (:func:`_plugin_refresh_needed`)
so a healthy install costs the sweep nothing: two ``claude`` invocations only fire when a
copy actually needs them, never on every tick.

**Part 1 — human-run, always on.** :func:`apply` (what ``chela update`` calls) and
:func:`check_and_notify` (the behind-notifier) only ever run when a human, or a script a
human runs, asks — the notifier only ever INFORMS, never pulls.

**Part 2 — :func:`auto_apply_sweep`, opt-in, off by default (``CHELA_AUTO_UPDATE`` /
:data:`chela.config.AUTO_UPDATE_ENABLED`).** Mirrors ``chela.automerge``'s contract: the
*same* :func:`apply` a human's own ``chela update`` runs — same dirty-tree / diverged-branch
refusal, nothing loosened — just invoked on the daemon's own hourly tick instead of a
human's say-so. An operator opts in only after reading that trade-off; it is never a
default and never inferred from any other flag.

The safety rail that makes ``apply()`` a command you can run (by hand OR unattended)
without reading its diff first: it refuses outright on a dirty working tree (never
clobbers local edits) and on a diverged branch (never forces a merge past commits only the
local checkout has) — both checked BEFORE anything touches disk. Only then does it pull,
re-sync dependencies with every extra installed (never ``--frozen``, which prunes extras
nobody asked to remove), and restart whatever ``chela-*`` PM2 services are actually
running.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from chela import config, hooks, notify
from chela.dispatcher import (
    GIT_NET_TIMEOUT_SECONDS,
    GIT_TIMEOUT_SECONDS,
    GitTimeout,
    _git,
    _git_ok,
    _git_out,
)

log = logging.getLogger(__name__)

# Non-git subprocesses (uv, pm2) get more slack than a git round-trip: a dependency
# re-sync or a service restart can legitimately take longer than GIT_TIMEOUT_SECONDS.
_SHELL_TIMEOUT_SECONDS = 300

# CMX-226/CMX-262: how many of `apply()`'s OWN subprocess calls sit on each side of that
# split, on its single slowest real path (behind > 0, not diverged, at least one chela-*
# PM2 service running, exactly one plugin marketplace to refresh — the common case, and the
# one the original CMX-199 outage was) — recount by reading `apply()` itself if this ever
# drifts. Git calls are further split by GIT_TIMEOUT_SECONDS (local, no network round-trip)
# vs GIT_NET_TIMEOUT_SECONDS (touches the remote, needs the same slack dispatcher.py already
# gives its own fetch/push calls — a link slow enough to blow a 30s budget on `git fetch`
# still finishes well inside 60s):
#   git/local (GIT_TIMEOUT_SECONDS each): `_is_dirty`'s `git status`, the pre-fetch `git
#   rev-parse @{u}` (`old_upstream`), `commits_behind`'s 2x `rev-parse` + 2x `rev-list`
#                                                                                 = 6 calls
#   git/net (GIT_NET_TIMEOUT_SECONDS each): `commits_behind`'s `fetch`, `git pull --ff-only`
#                                                                                 = 2 calls
#   shell (_SHELL_TIMEOUT_SECONDS each): `uv sync`, `pm2 jlist` (inside
#   `_running_pm2_services`), `pm2 restart`, and one marketplace's 2 `claude plugin ...`
#   calls (`_update_plugin`)                                                    = 5 calls
_APPLY_GIT_LOCAL_CALLS = 6
_APPLY_GIT_NET_CALLS = 2
_APPLY_SHELL_CALLS = 5


def apply_stuck_after_seconds() -> int:
    """The longest an honest, still-progressing :func:`apply` run can plausibly take.

    Every subprocess `apply()` shells out to is individually timeout-bounded (`_sh`/`_git`
    below), and any one of them failing — including via its own timeout — aborts `apply()`
    immediately: every step checks `_git_ok` / a non-zero return code and returns early
    rather than continuing. So the sum of the timeouts along its single slowest path (see
    `_APPLY_GIT_CALLS` / `_APPLY_SHELL_CALLS` above) IS a hard ceiling on how long a
    genuinely still-running `apply()` can take — past that sum, every call on that path has
    either finished or been killed. The only way a caller is still waiting past it (e.g. the
    `threading.Lock` `chela.dashboard.app`'s update-apply route holds while `apply()` runs)
    is that whatever held the lock died without releasing it — a wedged lock, not a slow run.

    Padded with half a shell timeout of headroom for the parts that sum doesn't model
    (interpreter/thread-scheduling overhead between calls) — not because the bound above is
    loose (it already assumes every call runs the full length of its own timeout, which is
    itself pessimistic), just so this doesn't fire on the very first second past it.

    Derived from `GIT_TIMEOUT_SECONDS` / `GIT_NET_TIMEOUT_SECONDS` / `_SHELL_TIMEOUT_SECONDS`
    rather than a literal so it tracks them if any ever changes — see `chela.dashboard.app`'s
    own use of this and the doctor fact (`runtime_truth.py`'s `dashboard.update_lock`) that
    reports a lock held past it.
    """
    return (
        _APPLY_GIT_LOCAL_CALLS * GIT_TIMEOUT_SECONDS
        + _APPLY_GIT_NET_CALLS * GIT_NET_TIMEOUT_SECONDS
        + _APPLY_SHELL_CALLS * _SHELL_TIMEOUT_SECONDS
        + _SHELL_TIMEOUT_SECONDS // 2
    )


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
    # "dirty-check" | "fetch" | "ff-check" | "rewrite-backup" | "rewrite-reset" | "pull"
    # | "uv-sync" | "pm2-restart" | "done"
    step: str = ""
    error: str = ""
    behind_before: int = 0
    restarted: list[str] = field(default_factory=list)
    rewrite_recovered: bool = False   # True if `apply()` recovered from a history rewrite
    backup_ref: str = ""              # where the pre-rewrite HEAD was preserved, if so
    plugin_updated: list[str] = field(default_factory=list)  # marketplaces refreshed
    plugin_error: str = ""            # set if a plugin refresh was attempted and failed


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
        try:
            fetch_cp = _git(repo, "fetch", timeout=GIT_NET_TIMEOUT_SECONDS, raise_on_timeout=True)
        except GitTimeout as e:
            return UpdateStatus(ok=False, error=str(e))
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


def _online_chela_services(repo: Path) -> list[dict]:
    """Every currently-online PM2 process named ``chela-*``, as its raw ``name`` +
    ``pm2_env`` fields. Empty (never an error) if PM2 isn't installed or nothing is
    running — a dev checkout run by hand has no services to restart, and that's a
    normal, not exceptional, outcome.
    """
    cp = _sh(["pm2", "jlist"], cwd=repo)
    if cp is None or cp.returncode != 0 or not cp.stdout.strip():
        return []
    try:
        procs = json.loads(cp.stdout)
    except ValueError:
        return []
    return [
        {"name": p.get("name", ""), "pm2_env": p.get("pm2_env") or {}}
        for p in procs
        if isinstance(p, dict)
        and str(p.get("name", "")).startswith("chela-")
        and (p.get("pm2_env") or {}).get("status") == "online"
    ]


def _running_pm2_services(repo: Path) -> list[str]:
    """Every currently-online PM2 process named ``chela-*``. See
    :func:`_online_chela_services` for the empty-is-normal contract.
    """
    return sorted(p["name"] for p in _online_chela_services(repo))


@dataclass(frozen=True)
class ServiceFreshness:
    """Whether the ``chela-*`` PM2 services running RIGHT NOW were started before the
    commit the checkout is sitting on right now existed — see :func:`services_running_stale_code`.
    """

    ok: bool
    stale: list[str] = field(default_factory=list)
    commit_epoch: int = 0
    error: str = ""


def _current_commit_epoch(repo: Path) -> int | None:
    """The committer-date (unix epoch seconds) of the checkout's current HEAD.

    A fixed property of the commit object itself, authored wherever the commit was first
    made — which is exactly why it is NOT, by itself, safe to compare a service's PM2
    start time against (see :func:`services_running_stale_code`): a commit is always
    committed before it is pulled, so this alone cannot tell "running old code" apart from
    "restarted in the ordinary gap between upstream authoring it and this box pulling it".
    """
    cp = _git(repo, "log", "-1", "--format=%ct")
    if not _git_ok(cp):
        return None
    try:
        return int(_git_out(cp))
    except ValueError:
        return None


def _checkout_arrival_epoch(repo: Path) -> int | None:
    """When HEAD's current commit actually landed in THIS checkout's working tree —
    the mtime of the reflog entry its last update (a pull, checkout, reset, or commit)
    wrote. Unlike :func:`_current_commit_epoch`'s committer date, this is pinned to this
    clone: it can't predate the moment the files actually arrived on disk here, which is
    what makes it the correct half of the comparison in
    :func:`services_running_stale_code`. ``None`` if it can't be determined (reflogs are
    disabled, or the path is unreadable) rather than a hard failure — the commit's own
    date, on its own, is still a valid (if weaker) floor.
    """
    cp = _git(repo, "rev-parse", "--git-path", "logs/HEAD")
    if not _git_ok(cp):
        return None
    # `--git-path` is relative to `repo` (not the caller's cwd) for a plain checkout, but
    # already absolute for a worktree — `Path.__truediv__` does the right thing for both:
    # joining onto an absolute right-hand side just returns that absolute path.
    try:
        return int((repo / _git_out(cp)).stat().st_mtime)
    except OSError:
        return None


def services_running_stale_code(repo: Path | None = None) -> ServiceFreshness:
    """Which running ``chela-*`` PM2 services predate the code now checked out.

    ``chela update`` pulls and restarts in the same step, and the ``repo.upstream_synced``
    doctor fact now catches a checkout that has simply fallen behind (CMX-199). Neither
    catches the residual gap this closes: an operator running a bare ``git pull`` by hand
    (bypassing ``chela update`` entirely) leaves the checkout genuinely in sync with its
    upstream — ``ahead == behind == 0`` — while every ``chela-*`` PM2 service just keeps
    running the process image it loaded at its OWN last start, oblivious to the new files
    on disk. ``repo.upstream_synced`` is a fact about the CHECKOUT; this is a fact about
    the RUNNING CODE, and a checkout can be perfectly "in sync" while every service
    serving traffic is still the old build.

    Compares each online service's PM2 ``pm_uptime`` (its own last-start timestamp, in
    epoch milliseconds) against the LATER of :func:`_current_commit_epoch` (when the
    commit was authored) and :func:`_checkout_arrival_epoch` (when it actually landed
    here): a service that started before either cannot possibly be running it, and a
    commit is always authored before it is pulled — using the commit date alone would miss
    a service that restarted in that ordinary gap, which is exactly the bare-`git pull`
    scenario this fact exists to catch. Never restarts anything itself — read-only,
    exactly like :func:`commits_behind`.
    """
    repo = repo or repo_root()
    commit_epoch = _current_commit_epoch(repo)
    if commit_epoch is None:
        return ServiceFreshness(ok=False, error="git log failed")
    arrival_epoch = _checkout_arrival_epoch(repo)
    threshold_epoch = max(commit_epoch, arrival_epoch) if arrival_epoch is not None else commit_epoch
    stale = sorted(
        svc["name"] for svc in _online_chela_services(repo)
        if isinstance(svc["pm2_env"].get("pm_uptime"), (int, float))
        and svc["pm2_env"]["pm_uptime"] / 1000 < threshold_epoch
    )
    return ServiceFreshness(ok=True, stale=stale, commit_epoch=commit_epoch)


def _plugin_marketplaces() -> list[str]:
    """Every marketplace slug an installed copy of chela's plugin is CONFIRMED to have come
    from — dedup, sorted for determinism.

    Confirmed means found via Claude Code's own ``installed_plugins.json``, not the
    cache-scan fallback (:func:`chela.hooks._cached_copies`, used when that registry is
    missing or unreadable): a marketplace *guessed* from a cache path is not something
    chela verified it installed, and mutating it on a guess is exactly the "which copy is
    this even" mistake the registry exists to prevent. Empty when nothing is installed (a
    genuinely different problem; ``chela doctor``'s ``plugin.installed`` fact reports it)
    or when the registry itself is unreadable (the fallback found copies, but none count).
    """
    return sorted({
        copy.marketplace for copy in hooks.installed_plugins()
        if copy.marketplace and copy.found_via == "installed_plugins.json"
    })


def _update_plugin(repo: Path) -> tuple[list[str], str]:
    """Refresh every CONFIRMED-installed copy of chela's plugin so it matches what was just
    pulled — the half of a release the git-pull/uv-sync/pm2-restart dance above never
    reaches (see the module docstring). Both ``claude`` subcommands are non-interactive.

    Best-effort and non-fatal on purpose: the server-side update already succeeded by the
    time this runs, and a missing/unauthenticated ``claude`` binary must not turn that
    success into a reported failure. Returns the marketplaces successfully refreshed and
    the first error hit, if any — stops at the first failure rather than half-refreshing
    every remaining marketplace on a broken ``claude`` invocation.

    Unconditional over ``_plugin_marketplaces()`` — the decision of WHETHER to call this at
    all belongs to :func:`_plugin_refresh_needed`, not here, so the mechanics stay testable
    on their own.
    """
    updated: list[str] = []
    for marketplace in _plugin_marketplaces():
        mp_cp = _sh(["claude", "plugin", "marketplace", "update", marketplace], cwd=repo)
        if mp_cp is None or mp_cp.returncode != 0:
            err = (mp_cp.stderr.strip() if mp_cp is not None
                   else "claude plugin marketplace update failed to run")
            return updated, f"marketplace update ({marketplace}): {err}"

        plugin_cp = _sh(["claude", "plugin", "update", f"{hooks.PLUGIN_NAME}@{marketplace}"],
                         cwd=repo)
        if plugin_cp is None or plugin_cp.returncode != 0:
            err = (plugin_cp.stderr.strip() if plugin_cp is not None
                   else "claude plugin update failed to run")
            return updated, f"plugin update ({marketplace}): {err}"

        updated.append(marketplace)
    return updated, ""


def _plugin_refresh_needed(copies: list[hooks.InstalledPlugin]) -> bool:
    """Whether at least one installed copy warrants running ``claude plugin ...`` at all.

    THREE different conditions, none a substitute for the others:

    - drift (:func:`chela.runtime_truth.installed_hooks_stale`) — a readable manifest that
      disagrees with what would render now (the CMX-41 port-drift class). Requires
      ``copy.hooks is not None``, so it is blind to the next case.
    - unreadable (``copy.hooks is None``) — the outage CMX-186 exists for: an installed
      copy Claude Code still points at, but whose ``hooks/hooks.json`` a sweep or a failed
      install left missing or broken. Drift can't see this; only checking ``hooks is None``
      directly does.
    - marketplace gone (:func:`chela.hooks.marketplace_missing`) — the outage CMX-321
      exists for: a manifest that reads PERFECTLY, with zero drift, from a copy Claude Code
      will still refuse to load because its marketplace vanished from its own registry.
      Neither of the above two conditions can see this — it is not about the manifest at
      all — so without this arm, a marketplace disappearing with no drift and no cache
      damage would never even trigger an attempt, let alone surface an error.

    Without this gate, every hourly ``auto_apply_sweep`` tick would run two
    network-touching ``claude`` invocations forever, even when nothing needs it.
    """
    from chela import runtime_truth  # lazy: runtime_truth -> capabilities -> update (cycle)

    return bool(copies) and (
        runtime_truth.installed_hooks_stale()
        or any(copy.hooks is None for copy in copies)
        or any(hooks.marketplace_missing(copy) for copy in copies)
    )


def _refresh_plugin_if_needed(repo: Path) -> tuple[list[str], str]:
    """Gate + drive the plugin half of a release. Called on EVERY ``apply()`` outcome that
    reaches this point — including the already-up-to-date path — since a plugin can go
    stale or unreadable with no commit involved (a cache sweep, a manual uninstall, a
    failed install): see the module docstring.

    Skips quietly (``[], ""``) when nothing needs it. When something does but every
    candidate copy was only found via the cache-scan fallback (the registry itself is
    missing or unreadable), skips with a reason instead of guessing which cache directory
    to mutate — chela did not confirm it installed that copy.
    """
    copies = hooks.installed_plugins()
    if not _plugin_refresh_needed(copies):
        return [], ""

    marketplaces = _plugin_marketplaces()
    if not marketplaces:
        return [], (
            "an installed copy looks stale or unreadable, but its marketplace could not "
            f"be confirmed (found via {copies[0].found_via}, not installed_plugins.json) "
            "— skipping rather than mutating a copy chela never confirmed it installed"
        )
    return _update_plugin(repo)


def _is_history_rewrite(repo: Path, old_upstream: str) -> bool:
    """Whether the fetch just moved the remote-tracking ref **non-fast-forward** — the
    fingerprint of a force-push (``git filter-repo`` + push): the tip ``@{u}`` had *before*
    this fetch (``old_upstream``, captured pre-fetch) is no longer an ancestor of the tip
    it has *now*.

    This is the reliable signal, NOT "HEAD and ``@{u}`` share no common ancestor": a
    mid-history ``filter-repo`` keeps every commit before the first change unmodified, so a
    common ancestor SURVIVES (only a full ``--orphan`` rewrite drops it) — a no-ancestor
    test would miss the exact scrub this exists for and fall through to the diverged
    refusal, stranding the adopter. An ordinary upstream that merely advanced keeps its old
    tip as an ancestor, so this stays False for it.

    ``git merge-base --is-ancestor`` exits 0 when ``old_upstream`` IS an ancestor of the new
    ``@{u}`` (an ordinary advance) and exactly 1 when it is NOT (a rewrite); any other
    nonzero exit (bad revision, repo trouble) is a different failure, NOT this case — so it
    reads as "not a rewrite" and falls through to the ordinary refusal rather than resetting
    on a guess. An empty ``old_upstream`` (no pre-fetch tip to compare) is likewise not a
    rewrite.
    """
    if not old_upstream:
        return False
    cp = _git(repo, "merge-base", "--is-ancestor", old_upstream, "@{u}")
    return cp is not None and cp.returncode == 1


def _recover_from_history_rewrite(repo: Path, branch: str) -> ApplyResult:
    """Recover from an upstream history rewrite WITHOUT ever destroying local commits:
    preserve the current (pre-rewrite) HEAD under a dedicated backup ref, THEN hard-reset
    the branch onto the rewritten upstream. Even if some of those commits were never
    published (real local work, not just now-invalid copies of what upstream rewrote),
    they stay fully reachable at the backup ref — nothing is deleted, only relocated off
    the branch tip. Returns an :class:`ApplyResult` with ``ok=True`` and ``backup_ref``
    set on success, or ``ok=False``/``step``/``error`` on failure — :func:`apply` returns
    this verbatim on failure and reads ``backup_ref`` off it on success.
    """
    head_sha = _git_out(_git(repo, "rev-parse", "HEAD"))
    backup_ref = f"refs/chela-backup/{branch}-{head_sha[:12]}"

    backup_cp = _git(repo, "update-ref", backup_ref, "HEAD")
    if not _git_ok(backup_cp):
        err = backup_cp.stderr.strip() if backup_cp is not None else "git update-ref failed to run"
        return ApplyResult(ok=False, step="rewrite-backup", error=err)

    reset_cp = _git(repo, "reset", "--hard", "@{u}")
    if not _git_ok(reset_cp):
        err = reset_cp.stderr.strip() if reset_cp is not None else "git reset failed to run"
        return ApplyResult(ok=False, step="rewrite-reset", error=err, backup_ref=backup_ref)

    log.warning(
        "⛑️ upstream history was rewritten (e.g. `git filter-repo` + force-push) — backed "
        "up the pre-rewrite HEAD to %s and reset %r onto the new history", backup_ref, branch,
    )
    return ApplyResult(ok=True, backup_ref=backup_ref)


def apply(repo: Path | None = None) -> ApplyResult:
    """The safe update sequence. Refuses before touching anything on a dirty tree;
    on a diverged branch, recovers safely if the divergence is actually an upstream
    history rewrite (see :func:`_is_history_rewrite`), otherwise still refuses. Then
    pulls (or, after a rewrite recovery, is already up to date), re-syncs, and restarts
    — in that order, every time.
    """
    repo = repo or repo_root()

    if _is_dirty(repo):
        return ApplyResult(ok=False, step="dirty-check",
                            error="working tree has uncommitted changes — refusing to "
                                  "update over local edits")

    # Capture the remote-tracking tip BEFORE the fetch moves it — comparing the old tip
    # against the new one is how a force-push (history rewrite) is told apart from an
    # ordinary advance (see _is_history_rewrite). "" if there is no upstream to read.
    old_upstream = _git_out(_git(repo, "rev-parse", "@{u}"))

    status = commits_behind(repo, fetch=True)
    if not status.ok:
        return ApplyResult(ok=False, step="fetch", error=status.error)

    rewrite_recovered = False
    backup_ref = ""
    if status.ahead > 0:
        if not _is_history_rewrite(repo, old_upstream):
            return ApplyResult(
                ok=False, step="ff-check", behind_before=status.behind,
                error=f"local branch is {status.ahead} commit(s) ahead of its upstream — "
                      "diverged, not fast-forwardable; resolve by hand",
            )
        recovery = _recover_from_history_rewrite(repo, status.branch)
        if not recovery.ok:
            return ApplyResult(ok=False, step=recovery.step, error=recovery.error,
                                behind_before=status.behind, backup_ref=recovery.backup_ref)
        rewrite_recovered = True
        backup_ref = recovery.backup_ref

    if status.behind == 0 and not rewrite_recovered:
        # A plugin can go stale or unreadable with NO commit involved (a cache sweep, a
        # manual uninstall, a failed install) — this check must not live behind "we just
        # pulled", or the exact outage it exists for never gets repaired (see module
        # docstring). It never touches the working tree, so it's safe on this early return.
        plugin_updated, plugin_error = _refresh_plugin_if_needed(repo)
        return ApplyResult(ok=True, step="done", behind_before=0,
                            plugin_updated=plugin_updated, plugin_error=plugin_error)

    if not rewrite_recovered:
        try:
            pull_cp = _git(repo, "pull", "--ff-only", timeout=GIT_NET_TIMEOUT_SECONDS,
                            raise_on_timeout=True)
        except GitTimeout as e:
            return ApplyResult(ok=False, step="pull", behind_before=status.behind, error=str(e))
        if not _git_ok(pull_cp):
            err = pull_cp.stderr.strip() if pull_cp is not None else "git pull failed to run"
            return ApplyResult(ok=False, step="pull", behind_before=status.behind, error=err)

    sync_cp = _sh(["uv", "sync", "--all-extras"], cwd=repo)
    if sync_cp is None or sync_cp.returncode != 0:
        err = sync_cp.stderr.strip() if sync_cp is not None else "uv sync failed to run"
        return ApplyResult(ok=False, step="uv-sync", behind_before=status.behind, error=err,
                            rewrite_recovered=rewrite_recovered, backup_ref=backup_ref)

    services = _running_pm2_services(repo)
    if services:
        restart_cp = _sh(["pm2", "restart", *services], cwd=repo)
        if restart_cp is None or restart_cp.returncode != 0:
            err = restart_cp.stderr.strip() if restart_cp is not None else "pm2 restart failed to run"
            return ApplyResult(ok=False, step="pm2-restart", behind_before=status.behind,
                                error=err, restarted=[], rewrite_recovered=rewrite_recovered,
                                backup_ref=backup_ref)

    plugin_updated, plugin_error = _refresh_plugin_if_needed(repo)

    return ApplyResult(ok=True, step="done", behind_before=status.behind, restarted=services,
                        rewrite_recovered=rewrite_recovered, backup_ref=backup_ref,
                        plugin_updated=plugin_updated, plugin_error=plugin_error)


UNKNOWN_BEHIND = -1
"""Sentinel ``previously_behind`` value meaning "already notified about an unknowable
state" (e.g. no upstream configured) — distinct from a real behind-count, which is never
negative, so it can't be mistaken for "0 commits behind"."""


def check_and_notify(previously_behind: int) -> int:
    """Called from the daemon loop on a bounded cadence. Edge-triggered exactly like
    ``notify.check_waiting``: logs (and, if configured, pushes) a heads-up once on the
    transition into "behind", not once per tick — a drumbeat of the same line is how an
    operator learns to ignore the log. Returns the behind-count to remember as the next
    call's ``previously_behind``.

    ``ok=True`` with a populated ``error`` (e.g. no upstream configured) is unknowable, not
    "up to date" — silently returning ``status.behind`` (0) here would make the daemon
    latch into permanent, unannounced silence. That state gets the same one-time heads-up
    treatment as a real update, tracked via :data:`UNKNOWN_BEHIND` so it can't be confused
    with a genuine 0.

    ⛔ NEVER calls :func:`apply` / ``git pull`` — informing is this function's entire job.
    """
    status = commits_behind(fetch=True)
    if not status.ok:
        return previously_behind  # can't tell right now; don't flap the edge on a blip
    if status.error:
        if previously_behind != UNKNOWN_BEHIND:
            log.warning("update status unknown: %s", status.error)
            if notify.enabled():
                notify.send(status.error, title="chela: update status unknown")
        return UNKNOWN_BEHIND
    if status.behind > 0 and previously_behind <= 0:
        log.warning("update available: %d commit(s) behind — run `chela update`", status.behind)
        if notify.enabled():
            notify.send(f"{status.behind} commit(s) behind — run `chela update`",
                         title="chela: update available")
    return status.behind


def auto_apply_enabled() -> bool:
    """Read live, never latched — an operator flips ``CHELA_AUTO_UPDATE`` on a running
    daemon the same way ``chela.automerge.enabled()`` reads ``CHELA_AUTO_MERGE``."""
    return config.AUTO_UPDATE_ENABLED


def auto_apply_sweep() -> ApplyResult:
    """The unattended half of self-update (CMX-148). Called from the daemon loop in place
    of :func:`check_and_notify` whenever :func:`auto_apply_enabled` is true, on the same
    hourly cadence (``main.UPDATE_CHECK_INTERVAL_SECONDS``).

    Runs the *exact same* :func:`apply` a human's own ``chela update`` runs — nothing here
    re-implements or loosens its dirty-tree / diverged-branch refusal. Stays silent only
    when there was truly nothing to do (``behind_before == 0`` and ``ok``); every real
    attempt — a successful pull-and-restart, or a refusal — is logged loudly and (if
    configured) pushed to :mod:`chela.notify`, because a stuck refusal (e.g. a dirty tree
    from a manual edit on the host) needs a human's attention to clear, and unlike an
    auto-merge candidate it will not resolve itself by waiting for the next tick.
    """
    result = apply()
    if result.ok and result.behind_before == 0:
        return result  # nothing was behind — the common case, kept quiet on purpose

    if result.ok:
        restarted = ", ".join(result.restarted) or "no services"
        plugin_note = (f"; refreshed plugin ({', '.join(result.plugin_updated)})"
                        if result.plugin_updated else "")
        if result.plugin_error:
            plugin_note = f"; plugin refresh FAILED: {result.plugin_error}"
        log.warning(
            "⬆️⚠️ auto-update: applied %d commit(s) UNATTENDED (CHELA_AUTO_UPDATE) — "
            "restarted %s%s", result.behind_before, restarted, plugin_note,
        )
        if notify.enabled():
            notify.send(
                f"applied {result.behind_before} commit(s), restarted {restarted}{plugin_note}",
                title="chela: auto-update applied",
            )
    else:
        log.error("auto-update: refused at step %r — %s", result.step, result.error)
        if notify.enabled():
            notify.send(f"refused at step {result.step!r}: {result.error}",
                         title="chela: auto-update FAILED")
    return result
