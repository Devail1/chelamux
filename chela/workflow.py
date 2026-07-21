"""Parse a ``WORKFLOW.md`` (YAML front matter + a prompt template body).

The file is HOT-RELOADED: :func:`load_workflow_cached` re-reads it when it
changes on disk and hands the caller the effective config, so editing
``concurrency.max``, ``polling.interval_ms``, ``agent.cmd`` or the prompt body
does not need a daemon restart — and, just as important, a YAML typo does not
take the daemon down. See :func:`load_workflow_cached` for the reload contract.
"""
from __future__ import annotations
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from chela import config

log = logging.getLogger(__name__)

PROJECT_KEY_RE = re.compile(r"^[A-Z]{2,5}$")

# Floor for a workflow-supplied poll interval. A `polling.interval_ms: 10` typo
# must not turn the dispatcher into a spin loop against git/gh.
MIN_POLL_INTERVAL_SECONDS = 5.0


@dataclass
class WorkflowDef:
    path: Path
    config: dict
    prompt_template: str

    def get(self, *keys, default=None):
        cur: object = self.config
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    @property
    def project_key(self) -> str:
        # Validated at load time, so this is guaranteed to be present and well-formed.
        return self.config["project_key"]


def _expand(value):
    if not isinstance(value, str):
        return value
    if value.startswith("$"):
        return os.environ.get(value[1:], "")
    return os.path.expandvars(os.path.expanduser(value))


def load_workflow(path: str | Path) -> WorkflowDef:
    """Read + parse ``path``. Raises on a bad file — see :func:`load_workflow_cached`
    for the long-running callers, which must degrade instead of dying."""
    p = Path(path).expanduser().resolve()
    return parse_workflow(p, p.read_text())


def parse_workflow(p: Path, text: str) -> WorkflowDef:
    config: dict = {}
    body = text

    if text.startswith("---\n") or text.startswith("---\r\n"):
        end = text.find("\n---", 4)
        if end == -1:
            raise ValueError(f"{p}: unterminated YAML front matter")
        front = text[4:end]
        body = text[end + 4:]
        parsed = yaml.safe_load(front) or {}
        if not isinstance(parsed, dict):
            raise ValueError(f"{p}: front matter must be a YAML map")
        config = parsed

    pk = config.get("project_key")
    if not isinstance(pk, str) or not PROJECT_KEY_RE.match(pk):
        raise ValueError(
            f"{p}: front matter is missing a valid `project_key` "
            f"(must match {PROJECT_KEY_RE.pattern!r}, e.g. `project_key: PROJ`); got {pk!r}"
        )

    return WorkflowDef(path=p, config=config, prompt_template=body.strip())


@dataclass
class WorkflowStatus:
    """The EFFECTIVE workflow for a path, plus any live parse error.

    ``workflow`` is the last known-good :class:`WorkflowDef` — it survives a bad
    edit. ``error`` is set whenever the file as it currently sits on disk cannot
    be parsed; when both are set, the caller is running on a stale-but-valid
    config and must say so (and, for the dispatcher, block new dispatches).
    """
    path: Path
    workflow: WorkflowDef | None = None
    error: str | None = None
    error_at: float | None = None
    loaded_at: float = 0.0
    reloads: int = 0          # successful re-reads after the first load
    digest: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None and self.workflow is not None


@dataclass
class _Entry:
    stat: tuple[int, int] | None = None
    status: WorkflowStatus = field(default_factory=lambda: WorkflowStatus(path=Path(".")))


_CACHE: dict[Path, _Entry] = {}


def _stat_key(p: Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def reset_cache() -> None:
    """Drop every cached workflow (tests; a fresh process starts empty anyway)."""
    _CACHE.clear()


def load_workflow_cached(path: str | Path) -> WorkflowStatus:
    """Load ``path``, re-reading it only when it has changed on disk.

    The reload contract (Symphony SPEC 6.2/6.3), in one place:

    * **Detect.** A stat gate (mtime_ns + size) decides whether to touch the
      file at all — this runs on every dispatcher tick, forever, so an unchanged
      workflow costs one ``stat`` and nothing else. A changed stat is followed by
      a content hash, so a touched-but-identical file is not re-parsed either.
    * **Re-apply.** A successful parse replaces the effective config. Callers
      pick it up on their *next* tick; an in-flight agent keeps the config it was
      launched with (the spec does not require restarting live sessions).
    * **Degrade, don't die.** A parse failure — including the half-written file
      an editor leaves behind mid-save — keeps the last known-good config in
      force and reports the error instead of raising. The caller is expected to
      keep reconciling and to stop dispatching until the file parses again.

    Never raises for a bad file: the error rides in ``WorkflowStatus.error``.
    """
    p = Path(path).expanduser().resolve()
    key = _stat_key(p)
    entry = _CACHE.get(p)

    # Unchanged on disk (and readable) → the cached status, no read, no parse.
    if entry is not None and key is not None and key == entry.stat:
        return entry.status

    prev = entry.status if entry is not None else None
    last_good = prev.workflow if prev else None

    try:
        text = p.read_text()
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        if prev is not None and prev.ok and digest == prev.digest:
            # Touched (or rewritten byte-identically) — re-stat, but don't re-parse.
            _CACHE[p] = _Entry(stat=key, status=prev)
            return prev
        wf = parse_workflow(p, text)
    except Exception as e:
        status = WorkflowStatus(
            path=p,
            workflow=last_good,
            error=f"{type(e).__name__}: {e}",
            error_at=time.time(),
            loaded_at=prev.loaded_at if prev else 0.0,
            reloads=prev.reloads if prev else 0,
            digest=prev.digest if prev else "",
        )
        if prev is None or prev.error != status.error:
            log.error(
                "%s is invalid: %s — keeping the last known-good config%s",
                p, status.error,
                "" if last_good else " (there is none: nothing has ever parsed)",
            )
        _CACHE[p] = _Entry(stat=key, status=status)
        return status

    status = WorkflowStatus(
        path=p, workflow=wf, loaded_at=time.time(), digest=digest,
        reloads=(prev.reloads + 1) if prev is not None else 0,
    )
    if prev is not None:
        log.info("%s changed — config re-applied from the next tick (no restart)", p)
    _CACHE[p] = _Entry(stat=key, status=status)
    return status


def workflow_error(path: str | Path) -> str | None:
    """The current parse error for ``path``, or None. Cheap enough to poll."""
    return load_workflow_cached(path).error


def poll_interval_seconds(wf: WorkflowDef | None, default: float) -> float:
    """The workflow's effective poll interval — ``polling.interval_ms``, else ``default``.

    Clamped to :data:`MIN_POLL_INTERVAL_SECONDS`; a missing or non-numeric value
    falls back to ``default`` rather than failing the tick.
    """
    if wf is None:
        return default
    raw = wf.get("polling", "interval_ms", default=None)
    if raw is None:
        return default
    try:
        secs = float(raw) / 1000.0
    except (TypeError, ValueError):
        log.warning("%s: polling.interval_ms is not a number (%r) — using %.0fs",
                    wf.path, raw, default)
        return default
    if secs <= 0:
        return default
    return max(MIN_POLL_INTERVAL_SECONDS, secs)


def render_prompt(template: str, vars: dict) -> str:
    out = template
    for k, v in vars.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def resolve_workspace_root(wf: WorkflowDef) -> Path:
    root = wf.get("workspace", "root", default="~/.chela/worktrees/default")
    root = _expand(root)
    p = Path(root)
    if not p.is_absolute():
        p = (wf.path.parent / p).resolve()
    return p


def default_chela_dir() -> Path:
    """Where a daemon with no ``CHELA_DIR`` set keeps its state — the REAL install."""
    return Path.home() / ".chela"


def workspace_escape(wf: WorkflowDef) -> str | None:
    """Why this process must NOT dispatch ``wf`` — or None when it may.

    ``CHELA_DIR`` isolates chela's STATE (scheduler.db, the runs table, the event log).
    It never isolated the WORKSPACE: that comes from ``workspace.root`` in the workflow
    file, which is read from the repo. So a daemon pointed at a scratch state dir still
    created worktrees under the REAL ``~/.chela/worktrees``, against the REAL tracker,
    and spawned REAL agents. That is not hypothetical — ``pytest`` did it (the shutdown
    test spawns the real ``chela run`` daemon under a ``tmp_path`` ``CHELA_DIR``), and on
    2026-07-14 it collided with a live run's worktree. On a clean box it would have
    launched agents.

    So: **a process whose ``CHELA_DIR`` is not the default owns nothing outside it**, and
    its workspace must live inside it. Anything else is refused — loudly, by the caller.

    Refuse rather than silently relocate the root: rewriting a path an operator
    configured leaves two half-populated worktree trees and no way to tell which one is
    live, and a daemon quietly working somewhere other than where it was told is the same
    class of bug as this one.
    """
    chela_dir = Path(config.CHELA_DIR).expanduser().resolve()
    if chela_dir == default_chela_dir().resolve():
        return None                                   # the real install: nothing to fence
    root = resolve_workspace_root(wf)
    if root == chela_dir or chela_dir in root.parents:
        return None
    return (
        f"{wf.path.name}: refusing to dispatch. CHELA_DIR is {chela_dir} (not the default "
        f"{default_chela_dir()}), but workspace.root resolves to {root}, which is OUTSIDE "
        "it — this daemon would create worktrees and spawn agents in a workspace it does "
        "not own. Point workspace.root inside CHELA_DIR, or run with the default CHELA_DIR."
    )
