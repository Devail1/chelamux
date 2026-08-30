from __future__ import annotations
import errno
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def ensure_worktree(
    repo_path: Path,
    task_id: str,
    base_branch: str,
    project_key: str,
    task_number: int,
    root: Path,
) -> tuple[Path, bool]:
    """Idempotent AND collision-proof. Returns (worktree_path, created) for task_id.

    `created` is True when this call freshly created the worktree — including when it
    had to clear a collision to do so — and False only when a LIVE worktree for the
    derived branch was reused (an idempotent re-dispatch). Callers use this to fire
    one-shot, per-worktree setup (e.g. `hooks.after_create`) exactly once on creation.

    Branch name follows the Jira-style scheme `{project_key.lower()}-{task_number}`
    (e.g. `proj-7`). The worktree directory is still keyed by `task_id` so the
    SHA-stable identity that powers idempotent dispatch survives — only the
    branch and tmux window name use the human-readable display.

    If a worktree already exists for the derived branch, return its path with
    created=False. Otherwise create one branched from base_branch (created=True).

    ⛔ `task_number` is minted per-workflow as `MAX(task_number)+1` over the runs the
    DB currently remembers (see `dispatcher._spawn`) — and the DB does not remember
    forever: `_prune_done_rows` drops old `done` rows past the retention window, and
    `delete_run` drops a `claimed`/`running` row outright. Both leave the branch name
    (and sometimes the worktree directory) behind on disk with nothing in the DB
    pointing at it any more. The NEXT task to land on that same number — a fresh
    task_id, unrelated to whatever used the slot before — must not have its dispatch
    fail just because `-b branch` refuses a branch that already exists, or `worktree
    add` refuses a directory that's still sitting there. Three collisions, handled in
    order, all resolved by clearing the leftover and creating fresh (never by reusing
    stale history — a reused slot is a NEW task, not a continuation of the old one):
      1. a dead worktree *administrative record* (directory gone, git doesn't know) —
         `worktree prune` drops it so it doesn't shadow the live check below;
      2. a *stale branch* with no worktree attached to it — force-deleted before `-b`;
      3. an *orphaned directory* at the target path that git has no record of at all —
         cleared before `worktree add`.
    A branch or directory that a LIVE worktree still owns is never touched here; that
    case returns via the reuse path above, before any of this runs.
    """
    branch = f"{project_key.lower()}-{task_number}"
    wt_path = (root / task_id).resolve()

    # (1) Drop administrative records for worktrees whose directory is already gone —
    # otherwise `git worktree list` still reports one as live and it would wrongly
    # shadow the reuse check below, or block `worktree add` on the path/branch later.
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "prune"], check=False, capture_output=True,
    )

    existing = _find_existing_worktree(repo_path, branch, root)
    if existing is not None and existing.is_dir():
        return existing, False

    # (2) A branch with this name exists but nothing above claimed a live worktree for
    # it — a stale leftover from a pruned/deleted run that reused this task_number.
    # Nothing owns it, so it's safe to drop and let `-b` recreate it fresh.
    if _branch_exists(repo_path, branch):
        log.warning("Branch %s exists with no live worktree; deleting stale branch", branch)
        subprocess.run(
            ["git", "-C", str(repo_path), "branch", "-D", branch],
            check=True, capture_output=True,
        )

    # (3) Something occupies wt_path that git has no record of (a crash mid-create, a
    # hand-deleted worktree that left files behind). `worktree add` refuses a
    # pre-existing target, so clear it rather than fail the dispatch on it.
    if wt_path.exists():
        log.warning("Worktree path %s exists but git has no record of it; clearing it", wt_path)
        shutil.rmtree(wt_path, ignore_errors=True)

    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", "-b", branch, str(wt_path), base_branch],
        check=True, capture_output=True,
    )
    return wt_path, True


class BranchGone(RuntimeError):
    """The branch a run's work lives on does not exist any more.

    Raised by :func:`attach_worktree` — and it is a HARD stop, not something to paper
    over: forking a fresh worktree from the base branch would silently throw away every
    commit the agent has already pushed and the PR that points at them. The run goes to
    ``needs_human`` instead (see ``dispatcher._respawn_rework``).
    """


def attach_worktree(repo_path: Path, branch: str, wt_path: Path, root: Path) -> tuple[Path, bool]:
    """The worktree for an EXISTING branch — reused if it is there, re-created if not.

    This is the rework loop's half of :func:`ensure_worktree`, and the difference is the
    whole point: ``ensure_worktree`` creates a branch (``-b``) forked from the base
    branch, which is exactly the wrong thing for a run that already has history, a
    pushed branch and an open PR. Here the branch is the input, not the output.

    ⛔ CMX-325: ``root`` is the configured worktrees root, passed straight through to
    :func:`_find_existing_worktree` — see its docstring for why a match outside ``root``
    (the incident: a branch checked out in the MAIN REPO, which ``git worktree list``
    reports exactly like any other worktree) must never be treated as reusable here. This
    is the write side of issue #398: without it, this function is exactly how the main
    repo's path was recorded as a run's ``worktree_path`` and then deleted as cleanup.

    Returns ``(path, attached)`` — ``attached`` is True when git had no worktree for the
    branch and one was checked out again (the original directory was cleaned up), False
    when the existing worktree was reused. Raises :class:`BranchGone` when the branch
    itself is gone: there is nothing to attach to, and inventing one would lose the work.
    """
    existing = _find_existing_worktree(repo_path, branch, root)
    if existing is not None and existing.is_dir():
        return existing, False

    if not _branch_exists(repo_path, branch):
        raise BranchGone(f"branch {branch!r} does not exist in {repo_path}")

    # git still has a worktree record but the directory is gone (a `rm -rf`, a cleanup):
    # prune it, or `worktree add` refuses the branch as "already checked out".
    if existing is not None:
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "prune"],
            check=False, capture_output=True,
        )

    wt_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", str(wt_path), branch],
        check=True, capture_output=True,
    )
    return wt_path, True


def detached_worktree(repo_path: Path, ref: str, wt_path: Path, root: Path) -> tuple[Path, bool]:
    """A THROWAWAY checkout of `ref`, DETACHED — for a reader that must not own the branch.

    The judge (see :mod:`chela.judge`) applies deliberate corruptions to files and re-runs
    the suite. ⛔ It must never do that in the run's OWN worktree: that directory is what a
    rework agent later commits and pushes from, so a mutation left behind there by a crash
    would be pushed to the PR by the very loop that spawned the judge. It also cannot simply
    check the branch out again — git refuses the same branch in two worktrees — which is why
    this is ``--detach``: the same commits, no claim on the branch.

    Idempotent: an existing directory at ``wt_path`` is reset to ``ref`` and reused (the
    judge re-runs on a new head sha), and a git record whose directory was deleted is pruned
    first. Returns ``(path, created)``. Raises :class:`BranchGone` when ``ref`` does not
    resolve — there is nothing to check out, and inventing something would be a lie.

    ``root`` (the configured worktrees root) is threaded through to :func:`remove_worktree`
    on the reset-failed/recreate path — see CMX-325.
    """
    if not _ref_exists(repo_path, ref):
        raise BranchGone(f"ref {ref!r} does not exist in {repo_path}")

    if wt_path.is_dir():
        # Reuse: hard-reset to the ref rather than deleting and re-adding — a `git worktree
        # add` onto a live directory fails, and a half-removed one is worse than either.
        reset = subprocess.run(
            ["git", "-C", str(wt_path), "checkout", "--detach", "--force", ref],
            capture_output=True, text=True,
        )
        if reset.returncode == 0:
            subprocess.run(
                ["git", "-C", str(wt_path), "clean", "-fdx", "-e", ".venv"],
                check=False, capture_output=True,
            )
            return wt_path, False
        log.warning("judge worktree %s could not be reset to %s (%s); re-creating it",
                    wt_path, ref, (reset.stderr or "").strip())
        remove_worktree(repo_path, wt_path, root)

    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "prune"], check=False, capture_output=True,
    )
    wt_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", "--detach", str(wt_path), ref],
        check=True, capture_output=True,
    )
    return wt_path, True


class NotAWorktree(Exception):
    """`wt_path` is a real repository (or contains one) — refusing to delete it."""


def refuse_if_not_a_worktree(repo_path: Path, wt_path: Path, root: Path) -> None:
    """Raise :class:`NotAWorktree` unless ``wt_path`` really is a throwaway worktree.

    🔴 CMX-320. On 2026-08-21 chela deleted its OWN main working copy,
    ``~/projects/chelamux``, as ordinary task-completion cleanup — twice, 90 minutes apart.
    A run row (`adopt-397`) had ``worktree_path`` recorded as the main repo rather than a
    throwaway worktree, because the task's branch was checked out THERE when its rework
    spawned: git refuses the same branch in two worktrees, so worktree resolution fell back
    to the existing checkout. Cleanup then removed "its worktree". `TODO.md` is gitignored,
    so it existed in no clone, no worktree and no branch — ~614KB of tracker history was
    unrecoverable. The second deletion took the fresh clone, because the poisoned column
    persists in the DB and re-arms on every daemon start (issue #398).

    ⛔ CMX-325: the FIRST check below is ``root`` membership — ``wt_path`` must resolve to
    ``root`` itself or somewhere under it. This is the invariant that actually matters:
    "the path equals the main repo" was only the ONE instance observed, not the whole
    hazard — a checkout of some OTHER repo, or this repo cloned somewhere unexpected, is
    just as unsafe a deletion target and is caught the same way. It runs on WHATEVER is
    stored in the row, regardless of how it got there (a rework write that predates this
    fix, a hand-edited DB, a future bug in a caller this function has never heard of) —
    that is what stops the poisoned-row-re-arms-on-restart half of issue #398, which a
    write-side guard alone cannot: the write-side fix (see :func:`_find_existing_worktree`)
    stops a NEW bad path from being recorded, but does nothing for a row already holding
    one when the daemon restarts and re-runs its completion cleanup.

    The structural checks below it are additional, independent signal — kept because they
    catch a case root-membership cannot: a real repository that somehow ended up recorded
    UNDER ``root`` (e.g. a clone, not a `git worktree add`, dropped directly into the
    worktrees directory by hand). It asserts a STRUCTURAL fact about the target, immediately
    before an irreversible operation:

    * a linked git worktree's ``.git`` is a FILE — a pointer into the parent repo's admin
      directory (``gitdir: /path/to/repo/.git/worktrees/<name>``);
    * a real clone's ``.git`` is a DIRECTORY — the object store itself.

    So a ``.git`` directory means the target is a repository, never a worktree, and deleting
    it destroys history and every ignored file with it. Also refuses the repo itself and any
    ANCESTOR of it, which no legitimate worktree can ever be.

    ⚠️ Deliberately raises rather than returning False: :func:`remove_worktree` returns False
    for ordinary, expected failures (a root-owned remnant it may not touch), and callers
    treat that as "left in place, carry on". A path that should never have been a deletion
    target is not that — it is a corrupt instruction, and it must be impossible to confuse
    with a routine no-op.
    """
    if not wt_path.exists():
        return                                  # nothing to delete, nothing to protect
    try:
        target = wt_path.resolve()
        repo = repo_path.resolve()
        worktrees_root = root.resolve()
    except OSError:                             # unreadable path — treat as unsafe
        raise NotAWorktree(f"{wt_path} could not be resolved — refusing to delete it")

    if not target.is_relative_to(worktrees_root):
        raise NotAWorktree(
            f"refusing to delete {target}: it is OUTSIDE the worktrees root "
            f"({worktrees_root}) — a cleanup target must live under it, whatever is "
            "recorded in the run row — see issue #398"
        )

    if target == repo:
        raise NotAWorktree(
            f"refusing to delete {target}: it IS the repository ({repo}), not a worktree"
        )
    if target in repo.parents:
        raise NotAWorktree(
            f"refusing to delete {target}: it CONTAINS the repository ({repo})"
        )
    if (target / ".git").is_dir():
        raise NotAWorktree(
            f"refusing to delete {target}: its .git is a DIRECTORY, so this is a real "
            "repository, not a linked worktree (a worktree's .git is a file). Deleting it "
            "would destroy history and every gitignored file in it — see issue #398"
        )


def remove_worktree(repo_path: Path, wt_path: Path, root: Path) -> bool:
    """Drop a worktree and its directory — survives every way it can go stale.

    Tries ``git worktree remove --force`` first — the normal case, a live worktree git
    knows about. When that fails and the directory is STILL there, two things can be true:

    * git has no administrative record of this path at all (an unregistered leftover — a
      crash mid-create, a hand-copied directory, ``git worktree list`` never heard of it).
      There is nothing for git to remove, so fall back to a direct recursive delete.
    * the delete itself fails with EPERM/EACCES — root-owned remnants a Docker build left
      behind (chela runs as the user, not root, so it can never touch them). This is NOT
      swallowed silently: it is logged loudly, naming the path and the owning uid, and the
      directory is left in place rather than half-deleted.

    Always ``git worktree prune``s afterward, so a directory that WAS removed doesn't
    linger as a dangling administrative record blocking the next ``worktree add``.

    ``root`` is the configured worktrees root (:func:`chela.workflow.resolve_workspace_root`)
    — REQUIRED, not optional, because :func:`refuse_if_not_a_worktree` uses it as the
    primary guard (CMX-325 / issue #398): a caller that cannot name its worktrees root has
    no business deleting anything on its behalf.
    """
    # ⛔ CMX-320/CMX-325: before ANY deletion path runs. `git worktree remove` would refuse
    # a real repository on its own, but the `shutil.rmtree` fallback below would not — and
    # that fallback exists precisely for paths git has no record of, which is exactly what
    # a wrongly-recorded main checkout (or any other out-of-root path) looks like from here.
    refuse_if_not_a_worktree(repo_path, wt_path, root)
    out = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(wt_path)],
        capture_output=True, text=True,
    )
    removed = out.returncode == 0

    if not removed and wt_path.exists():
        try:
            shutil.rmtree(wt_path)
            removed = True
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EPERM):
                try:
                    owner = wt_path.stat().st_uid
                except OSError:
                    owner = "?"
                log.warning(
                    "could not remove worktree %s: owned by uid %s, not this process's — "
                    "a container build likely wrote root-owned files here; rerun its build "
                    "step with `--user $(id -u):$(id -g)`. Left in place.",
                    wt_path, owner,
                )
            else:
                log.warning("could not remove worktree %s: %s", wt_path, e)

    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "prune"], check=False, capture_output=True,
    )
    return removed or not wt_path.exists()


def disk_usage_bytes(root: Path) -> int:
    """Best-effort recursive byte size of everything under ``root`` — the disk-budget
    rail's measurement (see ``config.worktree_disk_budget_bytes``). A plain ``os.walk``
    rather than shelling out to ``du``: it needs no coreutils flag that BSD/GNU disagree
    on, and it degrades entry-by-entry rather than all-or-nothing — a single unreadable
    subdirectory (a root-owned remnant from mode 4) is skipped, not fatal to the whole
    measurement. Symlinks are never followed, so a link back into the repo it was cloned
    from can't double-count or loop.
    """
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                else:
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def _ref_exists(repo_path: Path, ref: str) -> bool:
    out = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True, text=True,
    )
    return out.returncode == 0


def _branch_exists(repo_path: Path, branch: str) -> bool:
    out = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet",
         f"refs/heads/{branch}"],
        capture_output=True, text=True,
    )
    return out.returncode == 0


def is_inside_root(path: Path, root: Path) -> bool:
    """Is ``path`` ``root`` itself or somewhere under it, after resolving both?

    Unresolved comparison is exactly how a symlinked ``~/.chela`` or a relative
    ``root`` would slip past this — resolve both sides before comparing. Public: used
    both internally (see :func:`_find_existing_worktree`, :func:`refuse_if_not_a_worktree`)
    and by callers that must decide whether a STORED ``worktree_path`` is trustworthy
    before ever handing it to :func:`attach_worktree` (CMX-325, issue #398).
    """
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _find_existing_worktree(repo_path: Path, branch: str, root: Path) -> Path | None:
    """The live worktree checked out for ``branch``, if any — but only inside ``root``.

    ⛔ CMX-325 (issue #398, second half): ``git worktree list`` reports the repo's own
    MAIN working tree exactly like any linked worktree — it is simply the first entry,
    with no marker distinguishing it. `cmx-319`'s rework was checked out in the main
    repo when it respawned; git refuses the same branch in two worktrees, so this
    function (before this fix) happily returned the main repo's path as "the existing
    worktree for this branch", and callers (:func:`ensure_worktree`, :func:`attach_worktree`)
    trusted it enough to record it as a run's ``worktree_path``. Task-completion cleanup
    then deleted it — the main working copy, gone, twice (90 minutes apart, because the
    poisoned DB row re-armed on the next daemon restart).

    A match outside ``root`` is therefore never returned as reusable: it is logged and
    treated as if no worktree exists for the branch, which sends the caller down the
    "create fresh" path — and since the branch really is checked out elsewhere, `git
    worktree add` refuses it there too, surfacing as a loud error instead of a silent
    bad path. The invariant enforced is "inside the worktrees root", not "is not the
    main repo": a checkout anywhere else — another clone, another project's worktree —
    is caught by the same check, not just the one instance actually observed.
    """
    out = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "list", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout

    cur_path: str | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur_path = line[len("worktree "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            if ref == f"refs/heads/{branch}" and cur_path:
                found = Path(cur_path)
                if not is_inside_root(found, root):
                    log.warning(
                        "branch %s is checked out at %s, which is OUTSIDE the worktrees "
                        "root %s (likely the main repo or another checkout) — refusing to "
                        "treat it as a reusable worktree", branch, found, root,
                    )
                    return None
                return found
    return None
