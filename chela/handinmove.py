"""⚖️🔀 CMX-334: ``chela task-finished`` when ``origin/<base>`` moved WHILE its checks ran.

Seen 2026-10-20 on two hand-ins: the self-check took about an hour, the base moved by one or
two merges meanwhile, and the agent merged it and ran the whole hour again — every merge to
the base re-ran every hand-in in progress. The battery already measured the PR's own files,
so what is new is only what the base commits touched:

* the base is merged in, and only the tests that observe the files the new base commits
  touched are run (:func:`related_tests`; a code file no test observes runs the full suite);
* the experiments are re-run only for the PR files those base commits also touched;
* a conflict, a red test or a surviving experiment refuses, as before; unknown never passes.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from chela import basesync, judge, judge_select, sweep

CODE_SUFFIXES = (judge_select.PY_SUFFIXES + judge_select.JS_SUFFIXES
                 + judge_select.TS_SUFFIXES)


def related_tests(worktree: Path, files: list[str]) -> list[str] | None:
    """The test files that observe any of ``files``; ``None`` = run the full suite, because a
    code file is one no test observes, a non-code file is not prose (a lockfile, a config a
    test reads) or git cannot list the tests. Prose (docs, changelog) adds none."""
    listed = basesync._git(worktree, "ls-files", "--", "*.py")
    if listed.returncode != 0:
        return None
    tests = [f for f in listed.stdout.splitlines() if sweep.is_test_path(f)]
    selector = judge_select.Selector(worktree, [judge_select.Case(t, t, True) for t in tests])
    nodes: list[str] = []
    for rel in files:
        if not rel.endswith(CODE_SUFFIXES):
            if judge._is_docs_path(rel):
                continue
            return None
        sel = selector.select(rel)
        if sel.full:
            return None
        nodes += [n for n in sel.nodes if n not in nodes]
    return nodes


def _rerun_overlap(worktree: Path, workflow_path: str, experiments: str,
                   overlap: list[str]) -> tuple[int, str]:
    """Re-run the experiments that target an ``overlap`` file. ``(how many, error or "")``."""
    raw, err = judge.load_experiments(experiments)
    if err:
        return 0, err
    keep = [e for e in raw.get("experiments") or []
            if isinstance(e, dict) and os.path.normpath(str(e.get("file"))) in overlap]
    if not keep:
        return 0, ""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(dict(raw, experiments=keep), fh)
    try:
        out = judge.run_self_check(worktree, fh.name, workflow_path=workflow_path)
    finally:
        Path(fh.name).unlink(missing_ok=True)
    if not out.get("ok"):
        return len(keep), f"could not re-run them — {out.get('error')}"
    if out["blocking"] or out["cannot_verify"] or out.get("unrun"):
        return len(keep), (f"{out['blocking']} SURVIVED, CANNOT VERIFY: "
                           f"{out['cannot_verify'] or '-'}, {len(out.get('unrun') or [])} UNRUN")
    return len(keep), ""


def reconcile(worktree: Path, wf, workflow_path: str, base: str, since: str,
              experiments: str | None) -> dict:
    """``{"ok": True, "moved": None | record}`` or ``{"ok": False, "error"}``. ``since`` is the
    ``origin/<base>`` sha the checks started on, with HEAD in sync with it."""
    try:
        state = basesync.moved(worktree, base, since)
    except RuntimeError as e:
        return {"ok": False, "error": f"could not re-check the base after the checks — {e}"}
    if state is None:
        return {"ok": True, "moved": None}
    if state["conflicts"]:
        return {"ok": False, "error": "origin/" + base + " moved during the checks, and "
                + basesync.refusal(base, state["behind"], state["conflicts"])}
    pr_files = judge._changed_files(worktree, base)
    if pr_files is None:
        return {"ok": False, "error": "could not list this PR's own files"}
    overlap = sorted(set(pr_files) & set(state["dev_files"]))
    merge = basesync._git(worktree, "merge", "--no-edit", f"origin/{base}")
    if merge.returncode != 0:
        basesync._git(worktree, "merge", "--abort")
        return {"ok": False, "error": f"`git merge origin/{base}` failed: "
                                      f"{(merge.stderr or merge.stdout).strip()[:200]}"}
    tests = related_tests(worktree, state["dev_files"])
    cmd = judge.judge_test_cmd(wf)
    if not cmd and tests != []:
        return {"ok": False, "error": "the workflow sets no `judge.test_cmd` — nothing to "
                                      f"re-run after merging origin/{base}"}
    if tests is None or tests:
        if tests and judge_select.extendable(cmd):
            cmd = judge_select.extend(cmd, tests)
        res = judge.run_suite(cmd, worktree, timeout=judge.judge_suite_timeout(wf))
        if not res.green:
            return {"ok": False, "error": f"after merging origin/{base}, `{cmd}` is red "
                                          f"(exit {res.exit_code}):\n{res.tail[-2000:]}"}
    rerun, err = (_rerun_overlap(worktree, workflow_path, experiments, overlap)
                  if overlap and experiments else (0, ""))
    if err:
        return {"ok": False, "error": f"origin/{base} moved during the checks and touched "
                f"this PR's {', '.join(overlap)}; the {rerun} experiment(s) there: {err}"}
    return {"ok": True, "moved": {
        "from": since[:12], "to": state["to"][:12], "dev_files": len(state["dev_files"]),
        "overlap": overlap, "tests": "full suite" if tests is None else f"{len(tests)} file(s)",
        "experiments_rerun": rerun}}
