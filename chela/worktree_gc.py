"""🧹 Worktree GC (CMX-357) — collect the worktrees no run will ever come back to.

``reap_run_worktree`` / ``_cleanup_worktree_on_done`` only fire on a run's OWN lifecycle path,
so a closed run whose close kept its worktree, or a worktree whose row was pruned, purged or
requeued onto ``<task>-r2``, is never collected: on 2026-10-27 the chelamux root held 26
worktrees (1.9 GB), 21 of them belonging to no live run.

Once a day the daemon spawns ``chela ops worktrees --gc`` (:func:`spawn`, niced and detached,
never waited on). For each workflow's ``workspace.root`` it walks the worktrees GIT has
registered there (an unregistered directory is never touched) and collects one when

* no run row owns it (by the row's ``worktree_path``, its ``judge-<task>`` name, or its
  directory name) and it is :data:`GRACE_S` old, or
* its owning run is ``done``/``closed`` and has been for :data:`GRACE_S`.

and nothing is running in it (:func:`busy`: a process whose cwd is inside it). A clean
worktree is ``git worktree remove``d (the branch stays). A dirty one is salvaged first —
``git diff HEAD`` plus its untracked files, minus :data:`SALVAGE_SKIP_DIRS`, into
``$CHELA_DIR/worktree-salvage/<name>-<date>.tar.gz`` — and only removed when that archive is
written. Anything unknown (an unreadable runs table, /proc, git status, a missing
``ended_at``) keeps the worktree: unknown is never "collectable".
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

from chela import config, worktree

log = logging.getLogger(__name__)

GRACE_S = 3 * 24 * 3600
INTERVAL_S = 24 * 3600
GIT_TIMEOUT_S = 60
COLLECT_STATUSES = ("done", "closed")
RESERVED_NAMES = ("nightly-sweep",)       # sweep_nightly's own checkout: it removes it itself
SALVAGE_SKIP_DIRS = frozenset({
    ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", ".cache", ".tox", ".nox", ".uv-cache",
})
STATE_FILE = "worktree-gc.json"


class Unknown(Exception):
    """A read the decision depends on failed — the pass keeps every worktree it touches."""


def salvage_dir(chela_dir: Path | None = None) -> Path:
    return (chela_dir or config.CHELA_DIR) / "worktree-salvage"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                             timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        raise Unknown(f"`git {' '.join(args[:2])}` in {cwd} could not run: {e}") from e
    if out.returncode != 0:
        raise Unknown(f"`git {' '.join(args[:2])}` in {cwd} failed (exit {out.returncode}): "
                      f"{out.stderr.decode(errors='replace').strip()[:200]}")
    return out


def runs(db_path: Path) -> list[dict]:
    """Every run row's ``task_id``/``status``/``worktree_path``/``ended_at``, read-only.
    A missing or unreadable table raises :class:`Unknown`."""
    if not db_path.exists():
        raise Unknown(f"{db_path} does not exist")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            rows = conn.execute(
                "SELECT task_id, status, worktree_path, ended_at FROM runs").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise Unknown(f"runs table in {db_path} unreadable: {e}") from e
    return [{"task_id": t, "status": s, "worktree_path": w, "ended_at": e}
            for t, s, w, e in rows]


def registered(repo: Path, root: Path) -> list[Path]:
    """The LINKED worktrees git has on record for ``repo`` that live under ``root`` — a
    ``.git`` FILE, never a repository (``.git`` a directory), whatever lies where."""
    out = _git(repo, "worktree", "list", "--porcelain").stdout.decode(errors="replace")
    found = []
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):])
            if (path / ".git").is_file() and worktree.is_inside_root(path, root) \
                    and path.resolve() != root.resolve():
                found.append(path.resolve())
    return found


def owners(path: Path, rows: list[dict]) -> list[dict]:
    """The rows that own ``path``: by recorded ``worktree_path``, by ``judge-<task>``, or by
    a directory named after the task."""
    name = path.name
    task = name[len("judge-"):] if name.startswith("judge-") else name
    out = []
    for row in rows:
        recorded = row["worktree_path"]
        try:
            by_path = bool(recorded) and Path(recorded).resolve() == path
        except OSError:
            by_path = False
        if by_path or row["task_id"] == task:
            out.append(row)
    return out


def _ended(row: dict) -> float | None:
    try:
        ended = datetime.fromisoformat(str(row["ended_at"]))
    except (TypeError, ValueError):
        return None
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=timezone.utc)
    return ended.timestamp()


def _age_s(path: Path, now: float) -> float | None:
    try:
        newest = max(path.stat().st_mtime, (path / ".git").stat().st_mtime)
    except OSError:
        return None
    return now - newest


def busy(path: Path, root: Path, proc_root: Path) -> bool:
    """Is any process (other than this one and its ancestors) running with its cwd inside
    ``path``? An unreadable /proc raises :class:`Unknown`."""
    try:
        os.listdir(proc_root)
    except OSError as e:
        raise Unknown(f"{proc_root} unreadable: {e}") from e
    spared = worktree._ancestors(proc_root)
    return any(True for _ in worktree._procs_in(path, root.resolve(), proc_root, spared))


def verdict(path: Path, root: Path, rows: list[dict], now: float,
            proc_root: Path) -> tuple[bool, str]:
    """``(collect?, why)`` for one registered worktree."""
    if path.name in RESERVED_NAMES:
        return False, "reserved (the nightly sweep's checkout)"
    owned = owners(path, rows)
    for row in owned:
        if row["status"] not in COLLECT_STATUSES:
            return False, f"run {row['task_id']} is {row['status']}"
        ended = _ended(row)
        if ended is None:
            return False, f"run {row['task_id']} is {row['status']} with no readable ended_at"
        if now - ended < GRACE_S:
            return False, f"run {row['task_id']} went {row['status']} under 3 days ago"
    if not owned:
        age = _age_s(path, now)
        if age is None or age < GRACE_S:
            return False, "no run row, but touched under 3 days ago"
        why = "no run row"
    else:
        why = "; ".join(f"run {r['task_id']} {r['status']} ≥3 days" for r in owned)
    if busy(path, root, proc_root):
        return False, "a live process has its cwd in it"
    return True, why


def unpushed(path: Path) -> bool:
    """Does HEAD carry a commit no local branch and no remote-tracking ref holds? A detached
    checkout's commits would be lost with it, and they are not in ``git diff``."""
    return bool(_git(path, "rev-list", "--max-count=1", "HEAD", "--not", "--branches",
                     "--remotes").stdout.strip())


def dirty(path: Path) -> bool:
    return bool(_git(path, "--no-optional-locks", "status", "--porcelain",
                     "--untracked-files=all").stdout.strip())


def _skipped(rel: str) -> bool:
    return any(part in SALVAGE_SKIP_DIRS for part in Path(rel).parts)


def salvage(path: Path, why: str, dest_dir: Path, now: float) -> Path:
    """Archive what a dirty worktree holds that its branch does not: ``diff.patch`` (``git
    diff --binary HEAD``), ``untracked/<file>`` for each untracked, non-ignored file outside
    :data:`SALVAGE_SKIP_DIRS`, and ``meta.json``. Written to a temp name and renamed, so a
    path this returns is a complete archive."""
    patch = _git(path, "diff", "--binary", "HEAD").stdout
    listed = _git(path, "ls-files", "--others", "--exclude-standard", "-z").stdout
    untracked = [p for p in listed.decode(errors="surrogateescape").split("\0")
                 if p and not _skipped(p)]
    head = _git(path, "rev-parse", "HEAD").stdout.decode().strip()
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.decode().strip()
    meta = {"name": path.name, "path": str(path), "branch": branch, "head": head,
            "why": why, "untracked": untracked,
            "salvaged_at": datetime.fromtimestamp(now, timezone.utc).isoformat()}
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{path.name}-{datetime.fromtimestamp(now).strftime('%Y-%m-%d')}"
    dest, n = dest_dir / f"{stem}.tar.gz", 1
    while dest.exists():
        n += 1
        dest = dest_dir / f"{stem}-{n}.tar.gz"
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            for name, data in (("diff.patch", patch),
                               ("meta.json", json.dumps(meta, indent=2).encode())):
                info = tarfile.TarInfo(name)
                info.size, info.mtime = len(data), int(now)
                tar.addfile(info, io.BytesIO(data))
            for rel in untracked:
                tar.add(path / rel, arcname=f"untracked/{rel}", recursive=False)
        os.replace(tmp, dest)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise Unknown(f"could not write salvage archive {dest}: {e}") from e
    return dest


def collect(repo: Path, root: Path, *, db_path: Path, chela_dir: Path,
            proc_root: Path = Path("/proc"), now: float | None = None,
            dry_run: bool = False, remove=None) -> dict:
    """One GC pass over ``root``. Returns ``{"removed", "salvaged", "kept", "errors",
    "orphans"}``; ``orphans`` is every collectable worktree (with ``bytes`` and ``dirty``),
    which is all a ``dry_run`` does. ``remove`` is :func:`worktree.remove_worktree` (a seam)."""
    remove = remove or worktree.remove_worktree
    now = time.time() if now is None else now
    result: dict = {"root": str(root), "repo": str(repo), "removed": [], "salvaged": [],
                    "kept": [], "errors": [], "orphans": []}
    try:
        rows = runs(db_path)
        paths = registered(repo, root)
    except Unknown as e:
        result["errors"].append(str(e))
        log.warning("worktree gc: skipping %s — %s", root, e)
        return result
    for path in paths:
        try:
            ok, why = verdict(path, root, rows, now, proc_root)
            if not ok:
                result["kept"].append({"name": path.name, "why": why})
                continue
            if unpushed(path):
                result["kept"].append({"name": path.name,
                                       "why": "HEAD has commits on no branch or remote"})
                continue
            is_dirty = dirty(path)
        except Unknown as e:
            result["errors"].append(f"{path.name}: {e}")
            log.warning("worktree gc: keeping %s — %s", path, e)
            continue
        result["orphans"].append({"name": path.name, "why": why, "dirty": is_dirty,
                                  "bytes": worktree.disk_usage_bytes(path)})
        if dry_run:
            continue
        try:
            if is_dirty:
                archive = salvage(path, why, salvage_dir(chela_dir), now)
                result["salvaged"].append({"name": path.name, "archive": str(archive)})
                log.warning("worktree gc: %s (%s) had uncommitted changes — salvaged to %s",
                            path, why, archive)
            gone = remove(repo, path, root)
        except (Unknown, worktree.NotAWorktree) as e:
            result["errors"].append(f"{path.name}: {e}")
            log.error("worktree gc: NOT removing %s — %s", path, e)
            continue
        if gone:
            result["removed"].append(path.name)
            log.warning("worktree gc: removed %s (%s); its branch is kept", path, why)
        else:
            result["errors"].append(f"{path.name}: removal failed")
    return result


def _targets(workflow_paths) -> list[tuple[Path, Path]]:
    """``(repo, root)`` per readable workflow, each pair once. An unreadable workflow is
    logged and skipped — it names no root to sweep. So is one whose root is not this
    ``CHELA_DIR``'s to own (:func:`workflow.workspace_escape`): a scratch state dir's runs
    table knows none of the real root's runs, so every live worktree would read as orphan."""
    from chela.workflow import load_workflow, resolve_workspace_root, workspace_escape

    out: list[tuple[Path, Path]] = []
    for wf_path in workflow_paths:
        try:
            wf = load_workflow(wf_path)
        except Exception as e:  # noqa: BLE001 — any failure: no root known, nothing touched
            log.warning("worktree gc: workflow %s unreadable (%s) — skipped", wf_path, e)
            continue
        escape = workspace_escape(wf)
        if escape:
            log.warning("worktree gc: %s", escape)
            continue
        pair = (wf.path.parent.resolve(), resolve_workspace_root(wf).resolve())
        if pair not in out:
            out.append(pair)
    return out


def run(workflow_paths, *, dry_run: bool, chela_dir: Path | None = None,
        db_path: Path | None = None) -> list[dict]:
    """``chela ops worktrees [--gc]``: one :func:`collect` per workflow root. A real pass
    stamps :data:`STATE_FILE` BEFORE it starts, so a pass that crashes is retried in
    :data:`INTERVAL_S`, not respawned by the daemon on every tick; it rewrites it with the
    results when it ends."""
    chela_dir = chela_dir or config.CHELA_DIR
    db_path = db_path or chela_dir / "scheduler.db"
    state = chela_dir / "ops" / STATE_FILE
    started = time.time()
    if not dry_run:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"ts": started, "results": None}))
    results = [collect(repo, root, db_path=db_path, chela_dir=chela_dir, dry_run=dry_run)
               for repo, root in _targets(workflow_paths)]
    if not dry_run:
        state.write_text(json.dumps({"ts": started, "results": results}))
    return results


def due(chela_dir: Path | None = None, now: float | None = None) -> bool:
    """A pass is due when none was stamped in the last :data:`INTERVAL_S` — read from disk,
    so a daemon restart does not re-run it."""
    now = time.time() if now is None else now
    try:
        last = json.loads(((chela_dir or config.CHELA_DIR) / "ops" / STATE_FILE)
                          .read_text())["ts"]
    except (OSError, ValueError, KeyError, TypeError):
        return True
    return not isinstance(last, (int, float)) or now - last >= INTERVAL_S


def spawn_argv() -> list[str]:
    nice = ["nice", "-n", "19"] if shutil.which("nice") else []
    ionice = ["ionice", "-c", "3"] if shutil.which("ionice") else []
    return [*nice, *ionice, sys.executable, "-m", "chela.main", "ops", "worktrees", "--gc"]


def spawn(prev: subprocess.Popen | None = None) -> subprocess.Popen:
    """Start a GC pass detached and return at once; one still running is left alone."""
    if prev is not None and prev.poll() is None:
        return prev
    out = (config.CHELA_DIR / "ops")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "worktree-gc.log", "ab") as fh:
        return subprocess.Popen(spawn_argv(), stdin=subprocess.DEVNULL, stdout=fh,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                close_fds=True)


def render(results: list[dict], *, dry_run: bool) -> str:
    lines = []
    for r in results:
        orphans = r["orphans"]
        size = config.human_size(sum(o["bytes"] for o in orphans))
        n_dirty = sum(1 for o in orphans if o["dirty"])
        lines.append(f"{r['root']}: {len(orphans)} orphan worktree(s), {size}"
                     f" ({n_dirty} dirty)")
        for o in orphans:
            lines.append(f"  {o['name']:<24} {config.human_size(o['bytes']):>7}"
                         f"  {'dirty' if o['dirty'] else 'clean'}  {o['why']}")
        if not dry_run:
            lines.append(f"  removed {len(r['removed'])}, salvaged {len(r['salvaged'])}")
            lines += [f"  salvaged {s['name']} → {s['archive']}" for s in r["salvaged"]]
        lines += [f"  ⚠️ {e}" for e in r["errors"]]
    return "\n".join(lines) or "no workflow roots to scan"
