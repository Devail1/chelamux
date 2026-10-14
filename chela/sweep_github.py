"""⚖️🧹 CMX-97 — ``chela judge sweep --github``: the GitHub Action's half of the sweep.

The action (``action.yml`` at the repo root, run step ``scripts/judge-sweep-action.sh``) runs
the deterministic sweep on a pull request and needs only ``GITHUB_TOKEN`` — no Claude, no API
key. This module turns the sweep's result into what GitHub shows:

* ONE sticky PR comment (found again by :data:`COMMENT_MARKER` and edited in place), posted
  whenever there is something to say and updated back to ✅ once a later push is clean;
* the job's step summary, and the ``verdict`` / ``survivors`` step outputs.

⛔ Publishing never changes the verdict. A comment that cannot be posted (a fork's read-only
token, no ``gh``) is a warning; the job's exit code is still the sweep's own
(:data:`chela.sweep.EXIT_CODES`).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from chela import sweep

COMMENT_MARKER = "<!-- chela-judge-sweep -->"
MAX_LISTED = 50                 # bullets per section — a PR comment caps at 65,536 chars
GH_TIMEOUT_SECONDS = 60
_JQ_MARKED_IDS = f'.[] | select(.body | contains("{COMMENT_MARKER}")) | .id'

_HEADLINES = {
    sweep.V_CLEAN: "✅ no survivors",
    sweep.V_SURVIVORS: "❌ {n} survivor(s)",
    sweep.V_UNKNOWN: "⚠️ UNKNOWN — changed files in an unsupported language were not swept",
    sweep.V_CANNOT_VERIFY: "⚠️ cannot verify",
    sweep.V_ERROR: "⚠️ the sweep could not run",
}


def _code(text: object) -> str:
    """``text`` as ONE inline-code span, whatever backticks or newlines it carries."""
    s = re.sub(r"\s*[\r\n]+\s*", " ", str(text))
    longest = max((len(m) for m in re.findall(r"`+", s)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if s.startswith("`") or s.endswith("`") else ""
    return f"{fence}{pad}{s}{pad}{fence}"


def _one_line(text: object) -> str:
    return re.sub(r"\s*[\r\n]+\s*", " ", str(text))


def _bullets(items: list[str]) -> list[str]:
    out = [f"- {item}" for item in items[:MAX_LISTED]]
    if len(items) > MAX_LISTED:
        out.append(f"- …and {len(items) - MAX_LISTED} more (see the job log)")
    return out


def render_comment(result: dict) -> str:
    """The PR comment / step summary for one sweep result (a ``run_sweep`` dict)."""
    v = sweep.verdict(result)
    survivors = result.get("survivors") or []
    lines = [COMMENT_MARKER,
             f"### ⚖️🧹 chela judge sweep — {_HEADLINES[v].format(n=len(survivors))}", ""]
    if v == sweep.V_ERROR:
        lines.append(f"`chela judge sweep` could not run: {_one_line(result.get('error', ''))}")
    else:
        skipped = sum(len(result.get(k) or []) for k in ("unparseable", "not_applied", "invalid"))
        lines.append(
            f"{result.get('mutations', 0)} mutation(s) of the lines this PR changed vs "
            f"{_code(result.get('base', '?'))}: {result.get('killed', 0)} killed, "
            f"{len(survivors)} survived, {skipped} skipped · suite "
            f"{_code(result.get('test_cmd', '?'))} · {result.get('seconds', 0)}s")
        if result.get("cannot_verify"):
            lines += ["", f"**Cannot verify:** {_one_line(result['cannot_verify'])}"]
        if survivors:
            lines += ["", "Each mutation below **survived**: the suite stayed green with this "
                          "changed line corrupted. Add a test that goes red under it.", ""]
            lines += _bullets([f"{_code(r['key'])} {_code(r['mutation'])}" for r in survivors])
        unsupported = result.get("unsupported") or []
        if unsupported:
            lines += ["", f"**Not swept — UNKNOWN, not clean** ({sweep.SUPPORTED_NOTE}):", ""]
            lines += _bullets([_code(f) for f in unsupported])
    lines += ["", "<sub>Deterministic mutation sweep by "
                  "[chela](https://github.com/Devail1/chelamux) — no LLM, no API key.</sub>"]
    return "\n".join(lines) + "\n"


def pr_number(event_path: str | None) -> int | None:
    """The pull request number from ``$GITHUB_EVENT_PATH``; ``None`` when this is not a PR
    event or the payload cannot be read (no comment then — never a guess)."""
    try:        # no path at all: Path(None) is a TypeError, Path("") a directory (OSError)
        event = json.loads(Path(event_path).read_text())
        number = event["pull_request"]["number"]
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
        return None
    return number if type(number) is int and number > 0 else None


def _gh(runner, args: list[str], payload: str | None = None) -> tuple[str, str]:
    """``(stdout, error)`` for one ``gh`` call; ``error`` is "" only on exit 0."""
    try:
        out = runner(["gh", *args], input=payload, capture_output=True, text=True,
                     timeout=GH_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as e:
        return "", f"`gh {args[0]}` could not run: {e}"
    if out.returncode != 0:
        why = (out.stderr or out.stdout or "").strip().splitlines()
        return "", f"`gh {' '.join(args[:3])}` exited {out.returncode}: {why[-1] if why else ''}"
    return out.stdout or "", ""


def upsert_comment(repo: str, number: int, body: str, *, post_new: bool = True,
                   runner=subprocess.run) -> str:
    """Edit this action's existing comment on PR ``number`` in place, else post one (only if
    ``post_new``). Returns "" on success, else why not. ⛔ A comment list that cannot be read
    is an error, not "no comment yet" — that would stack a duplicate on every push."""
    listed, err = _gh(runner, ["api", "--paginate", f"repos/{repo}/issues/{number}/comments",
                               "--jq", _JQ_MARKED_IDS])
    if err:
        return err
    ids = listed.split()
    payload = json.dumps({"body": body})
    if ids:
        _, err = _gh(runner, ["api", "--method", "PATCH",
                              f"repos/{repo}/issues/comments/{ids[-1]}", "--input", "-"], payload)
        return err
    if not post_new:
        return ""
    _, err = _gh(runner, ["api", "--method", "POST", f"repos/{repo}/issues/{number}/comments",
                          "--input", "-"], payload)
    return err


def _append(path: str | None, text: str, what: str, warnings: list[str]) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        warnings.append(f"the {what} could not be written: {e}")


def publish(result: dict, *, comment: bool = True, environ=None,
            runner=subprocess.run) -> list[str]:
    """Write the step summary and outputs, and upsert the PR comment. Returns warnings —
    nothing here can change the verdict or the exit code."""
    env = os.environ if environ is None else environ
    warnings: list[str] = []
    v = sweep.verdict(result)
    body = render_comment(result)
    _append(env.get("GITHUB_STEP_SUMMARY"), body, "step summary", warnings)
    _append(env.get("GITHUB_OUTPUT"),
            f"verdict={v}\nsurvivors={len(result.get('survivors') or [])}\n",
            "step output", warnings)
    if not comment:
        return warnings
    repo, number = env.get("GITHUB_REPOSITORY", ""), pr_number(env.get("GITHUB_EVENT_PATH"))
    if not repo or number is None:
        warnings.append("not a pull_request run — no PR comment posted")
        return warnings
    err = upsert_comment(repo, number, body, post_new=v != sweep.V_CLEAN, runner=runner)
    if err:
        warnings.append(f"the PR comment could not be posted: {err}")
    return warnings
