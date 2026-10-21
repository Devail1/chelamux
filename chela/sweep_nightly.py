"""⚖️🧹🌙 CMX-299 — the NIGHTLY sweep over what the STANDARD tier merged without a judge.

CMX-296 (gate redesign 1b-i) lets a standard-tier PR — no core path, see
:mod:`chela.risktier` — merge on green CI with no judge round. This is the other half (1b-ii):
once a day nothing merged that way goes un-mutated for long.

* **When.** ``sweep.nightly_at`` in WORKFLOW.md (``"HH:MM"`` local; default ``02:00``;
  ``off``, ``never``, ``false`` or ``sweep.enabled: false`` turns it off). The daemon
  (:func:`daemon_tick`) spawns ``chela judge nightly-sweep`` DETACHED, one at a time (concurrency 1, and a lock file for a
  hand-run one), outside the dispatch slots, inside the shared memory slice the judge and the
  agents launch into (:func:`chela.memcap.wrap_launch_cmd`). A day is marked BEFORE the spawn,
  so a restart never sweeps the same day twice.
* **What.** Every first-parent commit on ``origin/<base>`` in the last 24 h whose files classify
  STANDARD (the same :func:`chela.risktier.classify` the merge gate uses — a core-tier PR met the
  judge already, a prose one has nothing to mutate). The lines swept are the ones that STILL
  blame to one of those commits at the tip, so a line a later core PR rewrote is not swept twice.
  ONE battery, through :func:`chela.sweep.sweep` — the judge's own mechanics and budget.
* **Filed.** Each surviving guard (one source line) is ONE tracker issue (labels ``risk:normal``
  and ``sweep``): its file, every mutation of it that survived with its category, and EVERY
  same-shape site in the tree (``git grep -F`` for the same stripped line). A survivor already
  filed is never filed again: its key — workflow, file, category, line text before and after,
  no line number — is kept in ``$CHELA_DIR/sweep-nightly/filed.json``, which outlives restarts.
* **Told.** ONE inbox notice per run: filed / clean / skipped, and why.
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
from datetime import datetime
from pathlib import Path

from chela import config, judge, risktier, sweep

log = logging.getLogger(__name__)

DEFAULT_AT = (2, 0)
WINDOW_SECONDS = 24 * 3600
# A night that could not look (a pause, a failed fetch, a red baseline) is caught up by the next
# one — from the last run that DID look, at most this far back.
MAX_CATCHUP_SECONDS = 7 * 24 * 3600
MAX_SITES = 100           # same-shape sites listed in one issue; the rest are counted
LABELS = ("risk:normal", "sweep")
INBOX_KIND = "nightly_sweep"
STATE_DIR_NAME = "sweep-nightly"
FLEET_PAUSE_FILE = "fleet-pause.json"      # chela.fleet.STATE_FILE_NAME (CMX-214)
GIT_TIMEOUT = 120
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

FILED, CLEAN, SKIPPED, CANNOT_VERIFY = "filed", "clean", "skipped", "cannot_verify"

_RE_AT = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_RE_BLAME = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)(?: \d+)?$")


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


def _ok_path(chela_dir: Path | None) -> Path:
    return state_dir(chela_dir) / "covered.json"


def window_start(wf, now: float, chela_dir: Path | None = None) -> float:
    """The oldest merge time swept: the last 24 h — or, after a night that could not look,
    back to the last run that did (at most :data:`MAX_CATCHUP_SECONDS`). An unreadable record
    is the plain 24 h, said in the log."""
    since = now - WINDOW_SECONDS
    try:
        last = _read_json(_ok_path(chela_dir)).get(str(wf.path))
    except ValueError as e:
        log.warning("nightly sweep: %s — sweeping the last 24h only", e)
        return since
    if isinstance(last, (int, float)) and not isinstance(last, bool) and last < since:
        since = max(float(last), now - MAX_CATCHUP_SECONDS)
    return since


def _record_covered(wf, now: float, chela_dir: Path | None) -> None:
    path = _ok_path(chela_dir)
    try:
        ok = _read_json(path)
    except ValueError:
        ok = {}
    ok[str(wf.path)] = now
    _write_json(path, ok)


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
    log_out = _git(repo, "log", "--first-parent", "--max-count=1000",
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


def same_shape_sites(worktree: Path, text: str) -> list[str] | None:
    """Every ``file:line`` of mutatable source whose stripped text is exactly ``text`` —
    ``None`` when ``git grep`` could not say (never "no other site")."""
    try:
        out = _git(worktree, "grep", "-n", "-I", "--full-name", "-F", "-e", text, ok=(0, 1))
    except _Git as e:
        log.warning("nightly sweep: same-shape sites of %r: %s", text, e)
        return None
    sites = []
    for raw in out.splitlines():
        rel, _, rest = raw.partition(":")
        ln, _, body = rest.partition(":")
        if ln.isdigit() and body.strip() == text and sweep._sweepable(rel):
            sites.append(f"{rel}:{ln}")
    return sites


# --- the filing ---------------------------------------------------------------------------


def survivor_key(wf_path: str, mut: sweep.Mutation) -> str:
    """Stable across nights and line shifts: no line number, no sha — what the guard IS."""
    raw = "\0".join((str(wf_path), mut.file, mut.kind, mut.before.strip(), mut.after.strip()))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def group_survivors(survivors: list[sweep.Mutation]) -> list[list[sweep.Mutation]]:
    """One group per surviving GUARD — a source line — with every mutation of it that
    survived, in the sweep's priority order."""
    groups: dict[str, list[sweep.Mutation]] = {}
    for m in survivors:
        groups.setdefault(m.key, []).append(m)
    return list(groups.values())


def render_issue(group: list[sweep.Mutation], sites: list[str] | None, head: str,
                 merged: dict[str, str]) -> tuple[str, str]:
    """``(title, description)`` for one surviving guard."""
    first = group[0]
    text = first.before.strip()
    kinds = ", ".join(dict.fromkeys(m.kind for m in group))
    title = f"Nightly sweep: {kinds} survived at {first.key}"
    lines = [
        "⚖️🧹 The nightly mutation sweep (CMX-299) found a guard on the base branch that no "
        "test can tell apart from its corruption. It merged from a STANDARD-tier PR, which "
        "gets no judge round.", "",
        f"**Category:** {kinds}",
        f"**File:** `{first.key}` (at `{head[:12]}`)",
    ]
    if merged.get(first.key):
        lines.append(f"**Merged in:** {merged[first.key]}")
    lines += ["", "**Mutation(s) that survived:**", ""]
    lines += [f"- `{m.kind}`: `{m.before.strip()}` → `{m.after.strip()}`" for m in group]
    lines += ["", f"**Same-shape sites** (every source line that reads exactly `{text}`):", ""]
    if sites is None:
        lines.append("- ⚠️ could not be listed (`git grep` failed) — check by hand")
    else:
        lines += [f"- `{s}`" + (" ← survived" if s == first.key else "")
                  for s in sites[:MAX_SITES]]
        if len(sites) > MAX_SITES:
            lines.append(f"- … and {len(sites) - MAX_SITES} more (`git grep -nF` the line)")
    lines += ["", "Add a test that goes RED under each mutation above — not a weaker "
                  "assertion — and check every same-shape site for the same gap."]
    return title, "\n".join(lines)


def tracker_filer(wf):
    """``file(title, description) -> identifier`` through the workflow's tracker, or None
    when its kind cannot create issues."""
    from chela.sources import get_source

    create = getattr(get_source(wf), "create_issue", None)
    if create is None:
        return None
    return lambda title, body: create(title, body, labels=LABELS)["identifier"]


# --- the run --------------------------------------------------------------------------------


def paused(chela_dir: Path) -> bool:
    """A fleet pause file that exists — even one we cannot parse — means paused."""
    return os.path.lexists(Path(chela_dir) / FLEET_PAUSE_FILE)


def run(wf, *, now: float | None = None, chela_dir: Path | None = None, filer=None) -> dict:
    """One nightly sweep of ``wf``, then ONE inbox notice. ``filer`` is the tracker seam
    (:func:`tracker_filer` when None)."""
    chela_dir = Path(chela_dir or config.CHELA_DIR)
    now = time.time() if now is None else now
    result = {"workflow": str(wf.path), "at": now, "filed": [], "already_filed": 0,
              "failed": [], "survivors": 0, "prs": []}
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
                except Exception as e:  # noqa: BLE001 — a crash is still ONE notice, said
                    log.exception("nightly sweep crashed")
                    result.update(status=CANNOT_VERIFY,
                                  why=f"the sweep crashed: {type(e).__name__}: {e}")
                if result.get("covered") and result["status"] != CANNOT_VERIFY \
                        and not result["failed"]:
                    _record_covered(wf, now, chela_dir)
    notify(result)
    return result


def _run(wf, now: float, chela_dir: Path, filer, result: dict) -> None:
    from chela.workflow import resolve_workspace_root
    from chela.worktree import detached_worktree, remove_worktree

    repo = wf.path.parent
    ref = f"origin/{wf.get('workspace', 'base_branch', default='dev')}"
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
                    chela_dir: Path, filer, result: dict) -> None:
    try:
        lines, origin = blamed_lines(worktree, standard)
    except _Git as e:
        result.update(status=CANNOT_VERIFY, why=f"could not blame the merged lines: {e}")
        return
    cfg = judge.judge_suite_config(wf)
    budget = sweep.resolve_budget(wf, seconds=wf.get("sweep", "budget_seconds"),
                                  max_mutations=wf.get("sweep", "max_mutations"))
    report = sweep.sweep(worktree, f"{ref}@{head[:12]} (standard-tier lines)",
                         cfg.test_cmd, timeout=cfg.suite_timeout_seconds,
                         select_tests=cfg.select_tests, budget=budget, lines=lines)
    result.update(mutations=len(report.results), unrun=len(report._with(sweep.UNRUN)),
                  unsupported=list(report.unsupported))
    if report.cannot_verify:
        result.update(status=CANNOT_VERIFY, why=report.cannot_verify)
        return
    survivors = [r.mutation for r in report.survivors]
    result["survivors"] = len(survivors)
    if not survivors:
        result.update(status=CLEAN, why="")
        return
    _file(wf, worktree, head, survivors, origin, standard, chela_dir, filer, result)


def _file(wf, worktree: Path, head: str, survivors: list[sweep.Mutation], origin: dict,
          standard: list[dict], chela_dir: Path, filer, result: dict) -> None:
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
    filer = filer if filer is not None else tracker_filer(wf)
    for group in group_survivors(survivors):
        keys = [survivor_key(str(wf.path), m) for m in group]
        if all(k in filed for k in keys):
            result["already_filed"] += 1
            continue
        if filer is None:
            result["failed"].append(f"{group[0].key}: tracker "
                                    f"{wf.get('tracker', 'kind')!r} cannot file issues")
            continue
        title, body = render_issue(group, same_shape_sites(worktree, group[0].before.strip()),
                                   head, merged)
        try:
            ident = filer(title, body)
        except Exception as e:  # noqa: BLE001 — one refused filing never loses the others
            result["failed"].append(f"{group[0].key}: {e}")
            continue
        for k in keys:
            filed[k] = {"issue": ident, "at": result["at"], "site": group[0].key}
        _write_json(keys_path, store)       # per issue: a crash never files one twice
        result["filed"].append(ident)
    result.update(status=FILED if result["filed"] or result["failed"] else CLEAN, why="")


def summary(result: dict) -> str:
    """The ONE inbox line for a run."""
    name = Path(result["workflow"]).parent.name or result["workflow"]
    head = f"🧹🌙 nightly sweep ({name})"
    prs = len(result.get("prs") or [])
    if result["status"] == SKIPPED:
        return f"{head}: skipped — {result['why']}"
    if result["status"] == CANNOT_VERIFY:
        return f"{head}: CANNOT VERIFY — {result['why']}"
    tail = (f" — {prs} standard-tier PR(s), {result.get('mutations', 0)} mutation(s)"
            + (f", {result['unrun']} UNRUN (budget)" if result.get("unrun") else "")
            + (f", {len(result['unsupported'])} file(s) UNKNOWN (not mutatable)"
               if result.get("unsupported") else ""))
    if result["status"] == CLEAN and not result["survivors"]:
        return f"{head}: clean — no survivors, nothing filed{tail}"
    parts = []
    if result["filed"]:
        parts.append(f"filed {', '.join(result['filed'])}")
    if result["already_filed"]:
        parts.append(f"{result['already_filed']} already filed")
    if result["failed"]:
        parts.append(f"{len(result['failed'])} NOT filed ({result['failed'][0]})")
    return f"{head}: {result['survivors']} survivor(s) — {'; '.join(parts)}{tail}"


def notify(result: dict) -> None:
    from chela import inbox

    result["summary"] = summary(result)
    log.info("%s", result["summary"])
    try:
        inbox.enqueue(INBOX_KIND, result["summary"], {k: v for k, v in result.items()
                                                      if k != "summary"})
    except Exception:  # noqa: BLE001 — the notice is the report, not the work
        log.exception("nightly sweep: the inbox notice failed")
