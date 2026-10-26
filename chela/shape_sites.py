"""⚖️🧩 CMX-335: every same-shape site of a blocking survivor, found MECHANICALLY in the PR's diff.

A blocking verdict used to name only the survivors the judge happened to pick. A PR with eight
``_end_agent(...)`` call sites and one survivor got one site fixed per round, four rounds in a
row (CMX-274). This groups the survivors by mutation SHAPE + TARGET and lists, for each group,
every changed line of the PR that has the same shape — found by a regex over the diff, never by
model recall — so the rework fixes the whole group at once.

Shapes, most specific first:

* ``call``    — a call whose name the corruption dropped or swapped; target = the bare name
  (``self._end_agent`` and ``_end_agent`` are one symbol). A site is a changed line calling it.
* ``compare`` — a comparison the corruption changed; target = ``lhs op rhs`` without spaces.
  A site is a changed line containing that comparison.
* ``line``    — anything else; target = the stripped line. A site is a changed line reading
  exactly that (the nightly sweep's fixed-string rule, :func:`chela.sweep_nightly.parse_grep`).
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from chela import sweep

MAX_SITES = 50

_RE_CALL = re.compile(r"(?<![\w$])([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(")
_RE_CMP = re.compile(r"([\w.\[\]'\"]+)\s*(==|!=|<=|>=|<|>|\bis not\b|\bis\b)\s*([\w.\[\]'\"]+)")
_RE_DEF = re.compile(r"^\s*(async\s+)?(def|function|class)\s")
_KEYWORDS = frozenset({"if", "elif", "while", "for", "return", "not", "and", "or", "in",
                       "print", "assert", "with", "lambda", "yield", "await"})


def _calls(text: str) -> list[str]:
    """Bare names called in ``text``, in order — never a ``def``/``function`` header."""
    names = []
    for line in text.splitlines():
        if _RE_DEF.match(line):
            continue
        names += [n.rsplit(".", 1)[-1] for n in _RE_CALL.findall(line)]
    return [n for n in names if n not in _KEYWORDS]


def _compare(line: str) -> str:
    m = _RE_CMP.search(line)
    return "".join(m.groups()).replace(" ", "") if m else ""


def shape(before: str, after: str) -> tuple[str, str]:
    """``(shape, target)`` of the corruption ``before`` → ``after`` (see the module doc)."""
    kept = Counter(_calls(after))
    for name in _calls(before):
        if kept[name] <= 0:
            return "call", name
        kept[name] -= 1
    gone = {ln.strip() for ln in after.splitlines()}
    lines = [ln.strip() for ln in before.splitlines() if ln.strip()]
    line = next((ln for ln in lines if ln not in gone), lines[0] if lines else "")
    cmp = _compare(line)
    return ("compare", cmp) if cmp else ("line", line)


def matches(kind: str, target: str, line: str) -> bool:
    """Whether source ``line`` has the shape ``(kind, target)``."""
    if kind == "call":
        return target in _calls(line)
    if kind == "compare":
        # Whole operands only: ``n<0`` is not a site of ``min<0`` or ``n<0x10``.
        return any("".join(m.groups()).replace(" ", "") == target
                   for m in _RE_CMP.finditer(line))
    return line.strip() == target


def diff_sites(worktree: Path, base_branch: str, kind: str, target: str,
               hidden: frozenset[str] = frozenset()) -> list[str] | None:
    """Every ``file:line`` the PR changed vs ``origin/<base_branch>`` with the shape, minus
    ``hidden`` — ``None`` when the diff cannot be read: unknown sites are never "no other
    site"."""
    if not base_branch or not target:
        return None
    lines, err = sweep.changed_lines(worktree, f"origin/{base_branch}")
    if err:
        return None
    sites = []
    for rel in sorted(lines):
        try:
            src = (worktree / rel).read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            return None
        sites += [f"{rel}:{n}" for n in sorted(lines[rel])
                  if n <= len(src) and matches(kind, target, src[n - 1])
                  and f"{rel}:{n}" not in hidden]
    return sites


def _lines_of(worktree: Path, outcomes: list) -> frozenset[str]:
    """Every ``file:line`` an outcome's ``before`` spans in the worktree (CMX-395: the held-out
    survivors, whose sites are never named)."""
    out = set()
    for o in outcomes:
        e = o.experiment
        try:
            text = (worktree / e.file).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        at = text.find(e.before)
        if at >= 0:
            first = text.count("\n", 0, at) + 1
            out.update(f"{e.file}:{n}" for n in range(first, first + e.before.count("\n") + 1))
    return frozenset(out)


def groups(worktree: Path, base_branch: str, outcomes: list, hidden: list = ()) -> list[dict]:
    """One ``{kind, target, guards, sites}`` per shape among ``outcomes`` (judge ``Outcome``s),
    in first-seen order. ``sites`` is ``None`` when the diff could not be read, and never names
    a line of a ``hidden`` (held-out) outcome."""
    out: dict[tuple[str, str], dict] = {}
    for o in outcomes:
        key = shape(o.experiment.before, o.experiment.after)
        g = out.setdefault(key, {"kind": key[0], "target": key[1], "guards": []})
        g["guards"].append(o.experiment.guard)
    for (kind, target), g in out.items():
        g["sites"] = diff_sites(worktree, base_branch, kind, target, _lines_of(worktree, hidden))
    return list(out.values())


def render(found: list[dict]) -> list[str]:
    """The verdict's ``Same-shape sites`` section — ``[]`` when there is no group."""
    if not found:
        return []
    parts = ["### 🧩 Same-shape sites — fix the WHOLE group, not only the site named above", "",
             "Each survivor is grouped by mutation shape and target. Every line this PR "
             "changed that has the same shape is listed, found mechanically from the diff — "
             "the same gap is likely at each one.", ""]
    for g in found:
        sites = g["sites"]
        count = "could not be listed (the diff was unreadable)" if sites is None else \
            f"{len(sites)} site(s) in this PR's diff (non-test .py/.js/.mjs searched)"
        parts += [f"**`{g['kind']}` on `{g['target']}`** — survived: "
                  f"{'; '.join(g['guards'])} — {count}", ""]
        parts += [f"- `{s}`" for s in (sites or [])[:MAX_SITES]]
        if sites and len(sites) > MAX_SITES:
            parts.append(f"- … and {len(sites) - MAX_SITES} more")
        parts.append("")
    parts += ["Make the fix cover EVERY listed site, and before hand-in corrupt EACH one by "
              "hand (or as a self-check experiment) and watch a test go red at every one.", ""]
    return parts
