"""⚖️🧹🌙 CMX-303 — the NIGHTLY sweep over what the STANDARD tier merged without a judge.

CMX-296 (gate redesign 1b-i) lets a standard-tier PR — no core path, see
:mod:`chela.risktier` — merge on green CI with no judge round. This is the other half (1b-ii,
CMX-299, landing in three slices): once a day nothing merged that way goes un-mutated for long.
Slice 1/3 is the scheduler (CMX-303); slice 2/3 (CMX-304) groups the survivors and finds
every same-shape site; slice 3/3 (CMX-305) files them in the tracker and tells the inbox.

* **When.** ``sweep.nightly_at`` in WORKFLOW.md (``"HH:MM"`` local; default ``02:00``;
  ``off``, ``never``, ``false`` or ``sweep.enabled: false`` turns it off). The daemon
  (:func:`daemon_tick`) spawns ``chela judge nightly-sweep`` DETACHED, one at a time
  (concurrency 1, and a lock file for a hand-run one), outside the dispatch slots, inside the
  shared memory slice the judge and the agents launch into (:func:`chela.memcap.wrap_launch_cmd`).
  A day is marked BEFORE the spawn, so a restart never sweeps the same day twice.
* **What.** It fetches ``origin`` and reads ``origin/<workspace.base_branch>``. Every
  first-parent commit on it in the covered window whose files classify STANDARD (the same
  :func:`chela.risktier.classify` the merge gate uses — a core or always-human PR met the judge
  or a human already, a prose one has nothing to mutate). A root commit is diffed against the
  empty tree. The lines swept are the ones that STILL blame to one of those commits at the tip,
  so a line a later core PR rewrote is not swept twice. The checkout is provisioned first
  (:func:`chela.judge.provision_suite_env`: venv and node_modules — CMX-325). ONE battery, through
  :func:`chela.sweep.sweep` — the judge's own mechanics, ``judge.select_tests``, suite timeout
  and budget. A mutation the budget never reached is UNRUN and a file no mutator handles is
  UNKNOWN: a night with either and no survivor says so, never ``clean``.
* **The covered window.** The last 24 h — or, after a night that could not look, back to the
  last run that did (at most :data:`MAX_CATCHUP_SECONDS`). Only a run that looked and
  finished (not CANNOT VERIFY, no refused filing, no mutation left UNRUN) moves it, in ``$CHELA_DIR/sweep-nightly/
  covered.json``.
* **Grouped.** One :class:`SurvivorGroup` per CATEGORY — mutation shape (its kind) plus target
  (the guard line's stripped text) — however many sites survived it. Each group lists EVERY
  same-shape site in the checkout: a FIXED-STRING ``git grep -F`` of the stripped line, so a
  regex metacharacter in it never drops a site. A group whose sites cannot be listed, or whose
  listing misses one of its own survivors, is not filed (fail closed; the next night retries).
* **Filed.** Each group goes to the :class:`Filer` unless EVERY survivor in it was filed
  before; a new site of a filed category files the group again. A survivor already filed is never
  filed again on its own: its key — workflow, file, kind, line text before and after, no line number — is
  kept in ``$CHELA_DIR/sweep-nightly/filed.json``, which outlives restarts and is written after
  EACH filing. The default filer is :class:`TrackerFiler`: ONE tracker issue per group, labelled
  :data:`LABELS` (``risk:normal``, ``sweep``), listing its survivors and its sites. A tracker that
  cannot create issues gets :class:`WouldFile`, which files nothing and records what it would
  have filed.
* **Told.** ONE inbox notice per run (:func:`summary`): what was filed, clean, or skipped and
  why, and the UNRUN / UNKNOWN counts. A run that filed nothing says so in that line.
* **Skipped** while a fleet pause (``chela dispatch --pause --all``) is active.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from chela import config, judge, risktier, sweep
from chela.workflow import dispatch_enabled

log = logging.getLogger(__name__)

DEFAULT_AT = (2, 0)
WINDOW_SECONDS = 24 * 3600
# A night that could not look (a pause, a failed fetch, a red baseline) is caught up by the next
# one — from the last run that DID look, at most this far back.
MAX_CATCHUP_SECONDS = 7 * 24 * 3600
LABELS = ("risk:normal", "sweep")
MAX_SITES = 100           # same-shape sites listed in one issue; the rest are counted
INBOX_KIND = "nightly_sweep"
STATE_DIR_NAME = "sweep-nightly"
FLEET_PAUSE_FILE = "fleet-pause.json"      # chela.fleet.STATE_FILE_NAME (CMX-214)
GIT_TIMEOUT = 120
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

SURVIVORS, CLEAN, SKIPPED, CANNOT_VERIFY = "survivors", "clean", "skipped", "cannot_verify"
# No survivor, but not clean either: the budget left mutations UNRUN, or a merged file is not
# mutatable (UNKNOWN). Neither is ever reported as clean.
UNRUN, UNKNOWN = "unrun", "unknown"

_RE_AT = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_RE_BLAME = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)(?: \d+)?\Z")


class _Git(Exception):
    pass


def state_dir(chela_dir: Path | None = None) -> Path:
    return Path(chela_dir or config.CHELA_DIR) / STATE_DIR_NAME


# --- when ---------------------------------------------------------------------------------


def nightly_at(wf) -> tuple[int, int] | None:
    """``(hour, minute)`` from ``sweep.nightly_at``; ``None`` when the nightly sweep is off. An
    unset value is :data:`DEFAULT_AT`; so is a malformed one, said in the log — a typo must
    not silently stop the only thing that mutation-tests standard-tier merges."""
    if wf.get("sweep", "enabled", default=True) is False:
        return None
    raw = wf.get("sweep", "nightly_at", default=None)
    if raw is None:
        return DEFAULT_AT
    if raw is False or (isinstance(raw, str) and raw.strip().lower() in ("off", "never")):
        return None
    if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw < 24 * 60:
        return divmod(raw, 60)     # YAML 1.1 reads an unquoted `2:30` as sexagesimal 150
    m = _RE_AT.match(str(raw).strip())
    if m:
        return int(m.group(1)), int(m.group(2))
    log.warning("%s: sweep.nightly_at %r is not HH:MM — using %02d:%02d", wf.path, raw,
                *DEFAULT_AT)
    return DEFAULT_AT


def _read_json(path: Path) -> dict:
    """``{}`` for a missing file. Raises ``ValueError`` for one that exists but cannot be read
    as a JSON object — the caller decides what an unknown means."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise ValueError(f"{path} is unreadable: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _last_path(chela_dir: Path | None) -> Path:
    return state_dir(chela_dir) / "last.json"


def due(wf, now: datetime, chela_dir: Path | None = None) -> bool:
    """Is today's sweep of ``wf`` due at ``now`` (local)? Past ``nightly_at`` and not yet
    started today. An unreadable day record is NOT due — never a second sweep on a guess."""
    if not dispatch_enabled(wf):
        return False                  # CMX-348: a keeper-only workflow merged nothing
    at = nightly_at(wf)
    if at is None or (now.hour, now.minute) < at:
        return False
    try:
        last = _read_json(_last_path(chela_dir))
    except ValueError as e:
        log.warning("nightly sweep: %s — not sweeping until it is fixed or removed", e)
        return False
    return last.get(str(wf.path)) != now.date().isoformat()


def mark_started(wf, now: datetime, chela_dir: Path | None = None) -> None:
    path = _last_path(chela_dir)
    try:
        last = _read_json(path)
    except ValueError:
        last = {}
    last[str(wf.path)] = now.date().isoformat()
    _write_json(path, last)


def _covered_path(chela_dir: Path | None) -> Path:
    return state_dir(chela_dir) / "covered.json"


def window_start(wf, now: float, chela_dir: Path | None = None) -> float:
    """The oldest merge time swept: the last 24 h — or, after a night that could not look,
    back to the last run that did (at most :data:`MAX_CATCHUP_SECONDS`). An unreadable record
    is the plain 24 h, said in the log."""
    since = now - WINDOW_SECONDS
    try:
        last = _read_json(_covered_path(chela_dir)).get(str(wf.path))
    except ValueError as e:
        log.warning("nightly sweep: %s — sweeping the last 24h only", e)
        return since
    if isinstance(last, (int, float)) and not isinstance(last, bool) and last < since:
        since = max(float(last), now - MAX_CATCHUP_SECONDS)
    return since


def _record_covered(wf, now: float, chela_dir: Path | None) -> None:
    path = _covered_path(chela_dir)
    try:
        covered = _read_json(path)
    except ValueError:
        covered = {}
    covered[str(wf.path)] = now
    _write_json(path, covered)


def spawn_argv(wf_path: Path) -> list[str]:
    return [sys.executable, "-m", "chela.main", "judge", "nightly-sweep", "--workflow",
            str(wf_path)]


def spawn(wf_path: Path, chela_dir: Path | None = None) -> subprocess.Popen:
    """Start ``chela judge nightly-sweep`` detached, in the judge's shared memory slice."""
    from chela import memcap

    out = state_dir(chela_dir)
    out.mkdir(parents=True, exist_ok=True)
    cmd = memcap.wrap_launch_cmd(shlex.join(spawn_argv(wf_path)))
    with open(out / "nightly.log", "ab") as fh:
        return subprocess.Popen(["sh", "-c", cmd], stdin=subprocess.DEVNULL, stdout=fh,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                close_fds=True)


def daemon_tick(workflows, prev: subprocess.Popen | None, now: datetime | None = None,
                chela_dir: Path | None = None) -> subprocess.Popen | None:
    """The daemon's half, once per loop: spawn the first DUE workflow's sweep — never a second
    while one is still running (concurrency 1). Returns the child to pass back next time."""
    if prev is not None and prev.poll() is None:
        return prev
    from chela.workflow import load_workflow_cached

    now = now or datetime.now()
    for wf_path in workflows:
        status = load_workflow_cached(wf_path)
        if not status.ok or not due(status.workflow, now, chela_dir):
            continue
        mark_started(status.workflow, now, chela_dir)
        log.info("nightly sweep: starting for %s", wf_path)
        return spawn(Path(wf_path), chela_dir)
    return prev


# --- which lines ----------------------------------------------------------------------------


def _git(cwd: Path, *args: str, ok=(0,)) -> str:
    try:
        out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                             errors="replace", timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise _Git(f"`git {' '.join(args[:3])}` could not run: {e}") from e
    if out.returncode not in ok:
        raise _Git(f"`git {' '.join(args[:3])}` failed (exit {out.returncode}): "
                   f"{(out.stderr or '').strip()[:200]}")
    return out.stdout


def merged_commits(repo: Path, ref: str, since: float, wf) -> list[dict]:
    """Each first-parent commit on ``ref`` committed at or after ``since``: ``{sha, subject,
    tier, files, introduced}`` — ``introduced`` is every commit it brought in (itself, or a
    merge's whole side branch). Raises :class:`_Git` on any read that fails."""
    out = []
    log_out = _git(repo, "log", "--first-parent", f"--since=@{int(since)}",
                   "--format=%H%x1f%ct%x1f%P%x1f%s", ref, "--")
    for raw in log_out.splitlines():
        sha, ct, parents, subject = (raw.split("\x1f") + ["", "", ""])[:4]
        if not ct.isdigit() or int(ct) < since:
            continue
        first = parents.split()[0] if parents.split() else EMPTY_TREE
        files = [f for f in _git(repo, "diff", "--name-only", "-z", "--no-renames", first, sha,
                                 "--").split("\0") if f]
        introduced = {sha}
        if len(parents.split()) > 1:
            introduced |= set(_git(repo, "rev-list", f"{first}..{sha}").split())
        tier = risktier.classify(files, risktier.always_human(wf), risktier.core(wf))[0]
        out.append({"sha": sha, "subject": subject, "tier": tier, "files": files,
                    "introduced": sorted(introduced)})
    return out


def blamed_lines(worktree: Path, commits: list[dict]) -> tuple[dict[str, set[int]],
                                                                dict[tuple[str, int], str]]:
    """``(lines, origin)``: the lines of the checkout at HEAD that still blame to one of
    ``commits``, and which of them each came from. Only source the sweep can judge — a
    mutatable file, or an unsupported one (reported UNKNOWN). Raises :class:`_Git`."""
    owner = {s: c["sha"] for c in commits for s in c["introduced"]}
    files = sorted({f for c in commits for f in c["files"]
                    if (sweep._sweepable(f) or sweep._unsupported(f))
                    and (worktree / f).is_file()})
    lines: dict[str, set[int]] = {}
    origin: dict[tuple[str, int], str] = {}
    for rel in files:
        for raw in _git(worktree, "blame", "--porcelain", "HEAD", "--", rel).splitlines():
            m = _RE_BLAME.match(raw)
            if m and m.group(1) in owner:
                ln = int(m.group(2))
                lines.setdefault(rel, set()).add(ln)
                origin[(rel, ln)] = owner[m.group(1)]
    return lines, origin


# --- the filing seam ------------------------------------------------------------------------


@dataclass(frozen=True)
class FileContext:
    """What a :class:`Filer` gets beside the surviving group: the run it came from."""
    workflow: Path
    worktree: Path           # the checkout at ``head`` — still there while the filer runs
    head: str
    ref: str
    merged_in: str           # "`<sha12>` <subject>" of the PR the guard line blames to, or ""
    chela_dir: Path


@dataclass(frozen=True)
class SurvivorGroup:
    """One CATEGORY of surviving guard: mutation shape (``mutation.kind``) plus target (the
    stripped line, :func:`category`), with every same-shape site of it in the checkout."""
    file: str                           # where the first survivor of the category is
    mutation: sweep.Mutation            # that survivor — the sweep's priority order
    survivors: tuple[sweep.Mutation, ...]   # every surviving mutation in the category
    sites: tuple[str, ...]              # EVERY "file:line" whose stripped text is the target
    key: str                            # :func:`category_key` — no file, no line number

    def as_dict(self) -> dict:
        return {"file": self.file, "key": self.key, "mutation": self.mutation.describe(),
                "kind": self.mutation.kind, "target": self.mutation.before.strip(),
                "survived": [m.key for m in self.survivors], "sites": list(self.sites)}


class Filer(Protocol):
    """Files ONE :class:`SurvivorGroup`. Returns the tracker identifier, or ``None`` when
    nothing was filed (the keys are then NOT recorded, so a later run files it). Raising
    refuses this filing only."""

    def __call__(self, group: SurvivorGroup, ctx: FileContext) -> str | None: ...


class WouldFile:
    """The :class:`Filer` for a tracker that cannot create issues: files NOTHING, and appends
    what it would have filed to ``$CHELA_DIR/sweep-nightly/would-file.jsonl``."""

    def __call__(self, group: SurvivorGroup, ctx: FileContext) -> None:
        path = state_dir(ctx.chela_dir) / "would-file.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"at": time.time(), "workflow": str(ctx.workflow), "site": group.mutation.key,
               "file": group.file, "key": group.key, "sites": list(group.sites),
               "head": ctx.head, "merged_in": ctx.merged_in,
               "mutations": [{"site": m.key, "kind": m.kind, "before": m.before.strip(),
                              "after": m.after.strip()} for m in group.survivors]}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        return None


def _code(text: str) -> str:
    """``text`` as one inline code span, fenced past any backtick run inside it."""
    ticks = "`" * (max((len(r) for r in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def render_issue(group: SurvivorGroup, ctx: FileContext) -> tuple[str, str]:
    """``(title, description)`` of the ONE issue for ``group``."""
    first = group.mutation
    text = first.before.strip()
    more = len(group.survivors) - 1
    title = f"Nightly sweep: {first.kind} survived at {first.key}" + (
        f" and {more} more site(s)" if more else "")
    lines = [
        "⚖️🧹 The nightly mutation sweep found a guard on the base branch that no test can "
        "tell apart from its corruption. It merged from a STANDARD-tier PR, which gets no "
        "judge round.", "",
        f"**Category:** `{first.kind}` on {_code(text)}",
        f"**Swept:** `{ctx.ref}` at `{ctx.head[:12]}`",
    ]
    if ctx.merged_in:
        lines.append(f"**Merged in:** {ctx.merged_in}")
    lines += ["", "**Mutations that survived:**", ""]
    lines += [f"- `{m.key}`: {_code(m.before.strip())} → {_code(m.after.strip())}"
              for m in group.survivors]
    lines += ["", f"**Same-shape sites** (every source line that reads exactly {_code(text)}):",
              ""]
    survived = {m.key for m in group.survivors}
    lines += [f"- `{s}`" + (" ← survived" if s in survived else "")
              for s in group.sites[:MAX_SITES]]
    if len(group.sites) > MAX_SITES:
        lines.append(f"- … and {len(group.sites) - MAX_SITES} more (`git grep -nF` the line)")
    lines += ["", "Add a test that goes RED under each mutation above — not a weaker "
                  "assertion — and check every same-shape site for the same gap."]
    return title, "\n".join(lines)


class TrackerFiler:
    """The tracker's :class:`Filer`: ONE issue per group through ``create_issue``, labelled
    :data:`LABELS`. A label the tracker could not attach is kept in :attr:`warnings` — the
    issue itself is filed."""

    def __init__(self, create):
        self.create = create
        self.warnings: list[str] = []

    def __call__(self, group: SurvivorGroup, ctx: FileContext) -> str:
        title, body = render_issue(group, ctx)
        out = self.create(title, body, labels=LABELS)
        ident = out["identifier"]
        self.warnings += [f"{ident}: {w}" for w in out.get("warnings") or ()]
        return ident


def default_filer(wf) -> Filer:
    """:class:`TrackerFiler` over the workflow's tracker, or :class:`WouldFile` when its kind
    cannot create issues."""
    from chela.sources import get_source

    create = getattr(get_source(wf), "create_issue", None)
    return TrackerFiler(create) if create is not None else WouldFile()


def survivor_key(wf_path: str, mut: sweep.Mutation) -> str:
    """Stable across nights and line shifts: no line number, no sha — what the guard IS."""
    raw = "\0".join((str(wf_path), mut.file, mut.kind, mut.before.strip(), mut.after.strip()))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def category(mut: sweep.Mutation) -> tuple[str, str]:
    """``(shape, target)``: the mutation's kind and the stripped text of the line it hit."""
    return mut.kind, mut.before.strip()


def category_key(wf_path: str, mut: sweep.Mutation) -> str:
    """The group's identity: workflow + :func:`category` — no file, no line number."""
    raw = "\0".join((str(wf_path), *category(mut)))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def group_survivors(survivors: list[sweep.Mutation]) -> list[list[sweep.Mutation]]:
    """One group per CATEGORY (:func:`category`) — every survivor of it, wherever it is — in
    the sweep's priority order."""
    groups: dict[tuple[str, str], list[sweep.Mutation]] = {}
    for m in survivors:
        groups.setdefault(category(m), []).append(m)
    return list(groups.values())


def parse_grep(out: str, text: str) -> list[str]:
    """``git grep -n -z`` output (``path NUL line NUL body``) → each ``"path:line"`` whose
    stripped body is EXACTLY ``text``, in mutatable source. A longer line merely containing
    ``text`` is not the same shape."""
    sites = []
    for raw in out.split("\n"):
        parts = raw.split("\0", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        rel, ln, body = parts
        if body.strip() == text and sweep._sweepable(rel):
            sites.append(f"{rel}:{ln}")
    return sites


def same_shape_sites(worktree: Path, text: str) -> list[str] | None:
    """Every ``file:line`` of mutatable source in ``worktree`` whose stripped text is exactly
    ``text`` — a FIXED-STRING grep (``-F``): ``.*[(`` is text, not a pattern. ``None`` when
    ``git grep`` could not say, never "no other site"."""
    if not text:
        return None
    try:
        out = _git(worktree, "grep", "-n", "-z", "-I", "--full-name", "--no-color",
                   "--no-column", "-F", "-e", text, "--", ok=(0, 1))
    except _Git as e:
        log.warning("nightly sweep: same-shape sites of %r: %s", text, e)
        return None
    return parse_grep(out, text)


def survivor_groups(wf_path: str, worktree: Path, survivors: list[sweep.Mutation],
                    ) -> tuple[list[SurvivorGroup], list[str]]:
    """``(groups, failed)``: one :class:`SurvivorGroup` per category, with its sites. A
    category whose sites cannot be listed — or whose listing misses one of its own survivors
    — is in ``failed`` instead (``"<site>: <why>"``): unknown sites are never "only these"."""
    groups, failed = [], []
    for members in group_survivors(survivors):
        first = members[0]
        sites = same_shape_sites(worktree, first.before.strip())
        if sites is None:
            failed.append(f"{first.key}: its same-shape sites could not be listed")
            continue
        missing = [m.key for m in members if m.key not in sites]
        if missing:
            failed.append(f"{first.key}: the same-shape listing misses {', '.join(missing)}")
            continue
        groups.append(SurvivorGroup(file=first.file, mutation=first, survivors=tuple(members),
                                    sites=tuple(sites), key=category_key(wf_path, first)))
    return groups, failed


# --- the run --------------------------------------------------------------------------------


def paused(chela_dir: Path) -> bool:
    """A fleet pause file that exists — even one we cannot parse — means paused."""
    return os.path.lexists(Path(chela_dir) / FLEET_PAUSE_FILE)


def base_ref(wf) -> str:
    return f"origin/{wf.get('workspace', 'base_branch', default='dev')}"


def run(wf, *, now: float | None = None, chela_dir: Path | None = None,
        filer: Filer | None = None) -> dict:
    """One nightly sweep of ``wf``, then ONE inbox notice. ``filer`` is the filing seam
    (:func:`default_filer` when None). Never raises an ``Exception``: a crash is a CANNOT
    VERIFY result."""
    chela_dir = Path(chela_dir or config.CHELA_DIR)
    now = time.time() if now is None else now
    result = {"workflow": str(wf.path), "at": now, "filed": [], "would_file": [],
              "already_filed": 0, "failed": [], "survivors": 0, "prs": [], "groups": [],
              "warnings": []}
    if paused(chela_dir):
        result.update(status=SKIPPED, why="a fleet pause (`chela dispatch --pause --all`) "
                                          "is active")
    elif not judge.judge_enabled(wf):
        result.update(status=SKIPPED, why="the judge is off for this workflow — nothing to "
                                          "mutate against")
    else:
        lock_path = state_dir(chela_dir) / "lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                result.update(status=SKIPPED, why="another nightly sweep is already running")
            else:
                try:
                    _run(wf, now, chela_dir, filer, result)
                except Exception as e:  # noqa: BLE001 — a crash is still ONE result, said
                    log.exception("nightly sweep crashed")
                    result.update(status=CANNOT_VERIFY,
                                  why=f"the sweep crashed: {type(e).__name__}: {e}")
                # ⛔ A night that could not verify (even after it read the commits: a red
                # baseline, a torn key store) or could not file must not move the window.
                # Nor does one whose budget left mutations UNRUN: those lines were never measured.
                if result.get("covered") and result["status"] != CANNOT_VERIFY \
                        and not result["failed"] and not result.get("unrun"):
                    _record_covered(wf, now, chela_dir)
    log.info("nightly sweep (%s): %s%s", wf.path, result["status"],
             f" — {result['why']}" if result.get("why") else "")
    notify(result)
    return result


def _run(wf, now: float, chela_dir: Path, filer: Filer | None, result: dict) -> None:
    from chela.workflow import resolve_workspace_root
    from chela.worktree import detached_worktree, remove_worktree

    repo = wf.path.parent
    ref = base_ref(wf)
    try:
        _git(repo, "fetch", "--quiet", "origin", ref.split("/", 1)[1])
        head = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
        since = window_start(wf, now, chela_dir)
        commits = merged_commits(repo, head, since, wf)
    except _Git as e:
        result.update(status=CANNOT_VERIFY, why=f"could not read {ref}: {e}")
        return
    standard = [c for c in commits if c["tier"] == risktier.STANDARD]
    result["covered"] = True
    window = ("in the last 24h" if since >= now - WINDOW_SECONDS
              else f"since {datetime.fromtimestamp(since).isoformat(timespec='minutes')}")
    result["head"] = head
    result["prs"] = [f"{c['sha'][:12]} {c['subject']}" for c in standard]
    result["not_swept"] = {t: sum(1 for c in commits if c["tier"] == t)
                           for t in (risktier.CORE, risktier.HUMAN, risktier.PROSE)}
    if not standard:
        result.update(status=SKIPPED, why=f"no standard-tier PR merged to {ref} {window}")
        return
    root = resolve_workspace_root(wf)
    worktree = (root / "nightly-sweep").resolve()
    try:
        detached_worktree(repo, head, worktree, root)
    except Exception as e:  # noqa: BLE001 — any checkout failure is a CANNOT VERIFY, said
        result.update(status=CANNOT_VERIFY, why=f"could not check out {head[:12]}: {e}")
        return
    try:
        _sweep_and_file(wf, worktree, head, ref, standard, chela_dir, filer, result)
    finally:
        try:
            remove_worktree(repo, worktree, root)
        except Exception:  # noqa: BLE001 — a leftover checkout is logged, never fatal
            log.exception("nightly sweep: could not remove %s", worktree)


def _sweep_and_file(wf, worktree: Path, head: str, ref: str, standard: list[dict],
                    chela_dir: Path, filer: Filer | None, result: dict) -> None:
    try:
        lines, origin = blamed_lines(worktree, standard)
    except _Git as e:
        result.update(status=CANNOT_VERIFY, why=f"could not blame the merged lines: {e}")
        return
    # ⛔ CMX-325: this checkout is never launched through an agent, so no `hooks.before_run`
    # built it — no venv, no node_modules. Provision it HERE, before the battery: the sweep's
    # own provisioning runs only after it has collected the mutations, and collecting a
    # .ts/.tsx file's already needs `typescript` (the first night was CANNOT VERIFY on that).
    env_problem = judge.provision_suite_env(worktree)
    if env_problem:
        result.update(status=CANNOT_VERIFY,
                      why=f"the nightly worktree could not be PROVISIONED: {env_problem}")
        return
    cfg = judge.judge_suite_config(wf)
    budget = sweep.resolve_budget(wf, seconds=wf.get("sweep", "budget_seconds"),
                                  max_mutations=wf.get("sweep", "max_mutations"))
    report = sweep.sweep(worktree, f"{ref}@{head[:12]} (standard-tier lines)",
                         cfg.test_cmd, timeout=cfg.suite_timeout_seconds,
                         select_tests=cfg.select_tests, provision=False, budget=budget,
                         lines=lines)
    unrun = [r.mutation.key for r in report._with(sweep.UNRUN)]
    result.update(mutations=len(report.results), unrun=len(unrun),
                  unrun_sites=sorted(set(unrun)), unsupported=list(report.unsupported))
    if report.cannot_verify:
        result.update(status=CANNOT_VERIFY, why=report.cannot_verify)
        return
    survivors = [r.mutation for r in report.survivors]
    result["survivors"] = len(survivors)
    if survivors:
        _file(wf, worktree, head, ref, survivors, origin, standard, chela_dir, filer, result)
    if result.get("status") is None:
        result.update(status=_quiet_status(result), why=_quiet_why(result))


def _quiet_status(result: dict) -> str:
    """A night with nothing (left) to file: CLEAN only when every mutation ran and every
    merged file was mutatable."""
    if result.get("unrun"):
        return UNRUN
    if result.get("unsupported"):
        return UNKNOWN
    return CLEAN


def _quiet_why(result: dict) -> str:
    if result.get("unrun"):
        return (f"{result['unrun']} mutation(s) the budget never reached are UNRUN — CANNOT "
                "VERIFY for their lines, not clean")
    if result.get("unsupported"):
        return (f"{', '.join(result['unsupported'])} UNKNOWN — no mutator handles them, not "
                "clean")
    return ""


def _file(wf, worktree: Path, head: str, ref: str, survivors: list[sweep.Mutation],
          origin: dict, standard: list[dict], chela_dir: Path, filer: Filer | None,
          result: dict) -> None:
    keys_path = state_dir(chela_dir) / "filed.json"
    try:
        store = _read_json(keys_path)
    except ValueError as e:
        # ⛔ Unknown is not "nothing filed yet": filing on a guess files duplicates.
        result.update(status=CANNOT_VERIFY, why=f"the filed-key store {e} — nothing filed")
        return
    filed = store.setdefault("keys", {})
    subjects = {c["sha"]: f"`{c['sha'][:12]}` {c['subject']}" for c in standard}
    merged = {f"{f}:{ln}": subjects.get(sha, "") for (f, ln), sha in origin.items()}
    filer = filer if filer is not None else default_filer(wf)
    groups, unlisted = survivor_groups(str(wf.path), worktree, survivors)
    result["failed"].extend(unlisted)
    result["groups"] = [g.as_dict() for g in groups]
    for group in groups:
        site = group.mutation.key
        keys = [survivor_key(str(wf.path), m) for m in group.survivors]
        # Per MUTATION: a new survivor category on a guard line filed before is filed again.
        if all(k in filed for k in keys):
            result["already_filed"] += 1
            continue
        ctx = FileContext(workflow=wf.path, worktree=worktree, head=head, ref=ref,
                          merged_in=merged.get(site, ""), chela_dir=chela_dir)
        try:
            ident = filer(group, ctx)
        except Exception as e:  # noqa: BLE001 — one refused filing never loses the others
            result["failed"].append(f"{site}: {e}")
            continue
        if ident is None:
            result["would_file"].append(site)
            continue
        result["filed"].append(ident)      # before the write: an issue made is reported
        for k in keys:
            filed[k] = {"issue": ident, "at": result["at"], "site": site}
        _write_json(keys_path, store)       # per issue: a crash never files one twice
    result["warnings"].extend(getattr(filer, "warnings", ()))
    if result["filed"] or result["failed"] or result["would_file"]:
        # A night with survivors AND unmeasured lines says both: the count alone hides it.
        result.update(status=SURVIVORS, why=_quiet_why(result))


# --- the notice -----------------------------------------------------------------------------


def summary(result: dict) -> str:
    """The ONE inbox line for a run: its outcome (and why), what was filed — or that nothing
    was — and the battery's counts, UNRUN and UNKNOWN included."""
    name = Path(result["workflow"]).parent.name or result["workflow"]
    status = result["status"]
    outcome = {SKIPPED: f"skipped — {result.get('why')}",
               CANNOT_VERIFY: f"CANNOT VERIFY — {result.get('why')}",
               SURVIVORS: f"{result['survivors']} survivor(s) in "
                          f"{len(result['groups'])} group(s)",
               CLEAN: "clean", UNRUN: "UNRUN, not clean", UNKNOWN: "UNKNOWN, not clean",
               }.get(status, status)
    filing = [f"filed {', '.join(result['filed'])}" if result["filed"] else "nothing filed"]
    if result["already_filed"]:
        filing.append(f"{result['already_filed']} group(s) already filed")
    if result["failed"]:
        filing.append(f"{len(result['failed'])} NOT filed ({result['failed'][0]})")
    if result["would_file"]:
        filing.append(f"{len(result['would_file'])} would be filed (the tracker cannot file "
                      "issues; see would-file.jsonl)")
    line = f"🧹🌙 nightly sweep ({name}): {outcome} — {'; '.join(filing)}"
    if "mutations" in result:
        line += (f" — {len(result['prs'])} standard-tier PR(s), {result['mutations']} "
                 f"mutation(s), {result.get('unrun', 0)} UNRUN")
        if result.get("unsupported"):
            line += f", {len(result['unsupported'])} file(s) UNKNOWN (no mutator)"
    if result["warnings"]:
        line += f" — ⚠️ {len(result['warnings'])} warning(s): {result['warnings'][0]}"
    return line


def notify(result: dict) -> None:
    """Queue the run's ONE inbox notice. A failing inbox is logged: the notice is the report,
    never the work."""
    from chela import inbox

    try:
        result["summary"] = summary(result)
    except Exception as e:  # noqa: BLE001 — run() never raises: a plain line instead
        log.exception("nightly sweep: the notice could not be rendered")
        result["summary"] = (f"🧹🌙 nightly sweep ({result.get('workflow')}): "
                             f"{result.get('status')} — the notice failed: {e!r}")
    log.info("%s", result["summary"])
    try:
        inbox.enqueue(INBOX_KIND, result["summary"],
                      {k: v for k, v in result.items() if k != "summary"})
    except Exception:  # noqa: BLE001 — the notice is the report, not the work
        log.exception("nightly sweep: the inbox notice failed")
