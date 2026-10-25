"""⚖️🔀 CMX-289: a judged PR whose head moved without touching any of its own files.

Seen 2026-10-20: a PR judged clean took a merge of ``origin/<base>`` that changed none of its
files. The re-judge wrote a FRESH experiment sample, found a new survivor, spent the last
rework round, and the run went to a human. Two rules close that:

* a clean verdict is CARRIED across a head move that touches no path of the PR's own diff
  (:func:`moved_pr_paths` is empty) — ``judge_sha`` is re-stamped with :data:`CARRIED`;
* any other re-judge re-runs the previous round's experiments FIRST (:func:`with_prior`), so
  lines already judged are never re-sampled; the judge writes new ones only for what changed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path

from chela import judge

log = logging.getLogger(__name__)

CARRIED = "carried: base-only merge"
GIT_TIMEOUT = 60


def moved_pr_paths(repo: str | Path, base: str, judged: str, head: str) -> set[str] | None:
    """The PR's own paths that changed between ``judged`` and ``head``, or ``None`` when git
    cannot say — unknown is never base-only. The PR's paths are its merge-base-relative diff
    at EITHER head, so a commit that reverts a PR file back to base still counts as moved."""
    old = judge._changed_files(Path(repo), base, judged)
    new = judge._changed_files(Path(repo), base, head)
    try:
        diff = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", "--no-renames", judged, head],
            capture_output=True, text=True, errors="replace", timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.warning("verdict carry: `git diff %s %s` could not run: %s", judged, head, e)
        return None
    if old is None or new is None or diff.returncode != 0:
        return None
    moved = {f for f in diff.stdout.splitlines() if f.strip()}
    return (set(old) | set(new)) & moved


def carried_detail(judged: str, head: str, detail: str | None) -> str:
    """The carried verdict's ``judge_detail``: ONE carry prefix, however many carries it took."""
    detail = detail or ""
    if detail.startswith(CARRIED):
        detail = detail.split("; ", 1)[1] if "; " in detail else ""
    return f"{CARRIED} ({judged[:12]} → {head[:12]}); {detail}"


def store_path(task_id: str) -> Path:
    from chela import config

    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", task_id) or "_"
    return config.CHELA_DIR / "judge-experiments" / f"{safe}.json"


def record(task_id: str, pr_url: str | None, sha: str | None, report: "judge.Report") -> None:
    """Keep the experiments this round actually measured (KILLED or SURVIVED — an INVALID one
    proved nothing) for the next round of the same PR, each marked ``survived``. Never raises:
    a lost record costs the next round its prior set, never this round's verdict."""
    try:
        items = [dict(o.experiment.as_dict(), held_out=o.held_out,
                      survived=o.verdict == judge.SURVIVED)
                 for o in report.outcomes if o.verdict in (judge.KILLED, judge.SURVIVED)]
        if not items:    # an unknown round measured nothing: keep the last real one
            return
        path = store_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"pr_url": pr_url or "", "sha": sha or "", "experiments": items}, fh)
        os.replace(tmp, path)        # a reader never sees a half-written record
    except Exception as e:  # noqa: BLE001 — by contract, nothing escapes into the verdict
        log.warning("judge: %s: could not keep this round's experiments: %s", task_id, e)


def load(task_id: str, pr_url: str | None) -> dict:
    """The previous round's record for THIS PR, or ``{}`` — a record left by another PR of
    the same task (a redispatch) is not this PR's prior round."""
    try:
        rec = json.loads(store_path(task_id).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(rec, dict) or not pr_url or rec.get("pr_url") != pr_url:
        return {}
    if not isinstance(rec.get("experiments"), list):
        return {}
    return rec


def with_prior(raw: dict, prior: dict, limit: int) -> tuple[dict, int]:
    """``raw`` with the prior round's experiments FIRST, minus any the judge proposed again
    verbatim; and how many were added.

    CMX-338: at most ``limit`` (the round's cap) are added, the prior round's SURVIVORS
    first. ``judge_run`` grows the cap by exactly that many, so a round runs at most
    2 × cap — the cap's worth of new experiments plus at most a cap's worth re-run — however
    many rounds the PR has had. Without the limit a round's record (up to 2 × cap) would
    become the next round's prior set and fill the whole grown cap, crowding out every new
    experiment."""
    def key(e):
        return (e.get("file"), e.get("before"), e.get("after")) if isinstance(e, dict) else None

    new = raw.get("experiments")
    new = new if isinstance(new, list) else []
    seen = {key(e) for e in new}
    added = []
    for e in prior.get("experiments") or []:
        if key(e) not in seen:    # a verbatim repeat, new or prior, never takes a slot twice
            seen.add(key(e))
            added.append(e)
    added.sort(key=lambda e: not (isinstance(e, dict) and e.get("survived") is True))
    added = [{k: v for k, v in e.items() if k != "survived"} if isinstance(e, dict) else e
             for e in added[:max(0, limit)]]
    return dict(raw, experiments=added + new), len(added)


def prompt_note(prior: dict, head: str, limit: int) -> str:
    """The judge prompt's view of a prior round — "" when there is none, or it was this head.
    The count is what :func:`with_prior` will actually re-run: at most ``limit``."""
    sha = prior.get("sha") or ""
    n = min(len(prior.get("experiments") or []), max(0, limit))
    if not sha or not n or sha == head:
        return ""
    return (f"\n   🔁 **This PR was judged before, on `{sha[:12]}`.** chela re-runs that round's "
            f"{n} experiment(s) itself, first. Write experiments ONLY for lines changed since "
            f"then (`git diff {sha} HEAD`) — never a fresh sample of lines already judged.")
