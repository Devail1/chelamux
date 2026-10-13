"""⚖️🧹 ``chela judge sweep`` — a MECHANICAL mutation pass over every conditional a branch changed.

CMX-88, measured 2026-10-09 → 10-12: 82 judge rounds, a median 26-minute battery each, about
two rounds per merged task. The judge is the bottleneck, and most of what it finds is the
same shape: a changed ``if`` no test can tell apart from ``if False``. ``chela judge
self-check`` runs mutations the AGENT chooses, so it tests the guards the agent thought of.
On CMX-87 the orchestrator ran a dumb pass instead — every ``if X:`` in one file became
``if False and (X):`` — and found 17 uncaught checks in ~10 minutes, where the judge was
finding 3-4 per ~35-minute round.

This module is that dumb pass, scoped to the lines the branch CHANGED vs its base:

* ``if X:`` / ``elif X:`` / JS ``if (X)`` ⇒ ``if False and (X)`` — and the ``True or (X)``
  twin when the ``if`` has an ``else``/``elif`` to fall into;
* a ``return``/``CompletedProcess`` code literal ``0``↔``1``; ``return None``↔``return
  <expr>`` in a function that has both;
* comparison flips ``==``↔``!=``, ``<``↔``>=``, ``>``↔``<=`` (JS: ``===``↔``!==`` too).

⛔ It decides nothing new. Every mutation goes through the judge's own mechanics —
:func:`chela.judge.apply_mutation` (anchor occurs once, read back from disk),
:func:`chela.judge.parse_check`, :func:`chela.judge._measure` (per-mutation test selection,
subset survivors confirmed on the full suite) and :func:`chela.judge.adjudicate` — so a
survivor here is a survivor in exactly the judge's sense.

⛔ A MUTATION MUST NEVER OUTLIVE THE SWEEP. Each one is restored in a ``finally``, and before
it is written a RECOVERY MARKER (:data:`RECOVERY_MARKER`, inside the worktree) records the
file's original bytes. A sweep killed mid-mutation — SIGKILL, a closed pane — leaves the
marker behind, and the next sweep restores the file from it BEFORE doing anything else.
SIGTERM/SIGHUP are turned into an exception while a sweep runs, so they take the ``finally``
path instead of skipping it.
"""
from __future__ import annotations

import ast
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from chela import judge

RECOVERY_MARKER = ".chela-sweep-recovery.json"
PY_SUFFIXES = (".py",)
JS_SUFFIXES = (".js", ".mjs")

# Per-mutation statuses. KILLED / SURVIVED / INVALID are the judge's own verdicts; the two
# below are mutations that never reached a suite, and are NEVER counted as killed.
UNPARSEABLE = "UNPARSEABLE"   # the mutated file did not parse — skipped, said out loud
NOT_APPLIED = "NOT_APPLIED"   # the anchor could not be applied/proven — skipped, said out loud

_PY_FLIP = {ast.Eq: ("==", "!="), ast.NotEq: ("!=", "=="), ast.Lt: ("<", ">="),
            ast.GtE: (">=", "<"), ast.Gt: (">", "<="), ast.LtE: ("<=", ">")}
_JS_FLIP = {"===": "!==", "!==": "===", "==": "!=", "!=": "==",
            "<": ">=", ">=": "<", ">": "<=", "<=": ">"}
# An operator with whitespace on both sides — the only spelling matched, so `=>`, `>>`,
# `<<` and generics are never mistaken for a comparison.
_RE_JS_CMP = re.compile(r"(?<=\s)(===|!==|==|!=|<=|>=|<|>)(?=\s)")
_RE_JS_IF = re.compile(r"\bif\s*\(")
_RE_JS_RET = re.compile(r"\breturn\s+([01])\b")
_RE_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Mutation:
    """One mechanical corruption of one changed line. ``before``/``after`` are the WHOLE line
    (no newline); :func:`_anchor` widens them to a span that occurs exactly once."""
    file: str
    line: int
    kind: str
    before: str
    after: str

    @property
    def key(self) -> str:
        return f"{self.file}:{self.line}"

    def describe(self) -> str:
        return f"{self.kind}: `{self.before.strip()}` → `{self.after.strip()}`"


@dataclass
class SweepResult:
    mutation: Mutation
    status: str
    reason: str = ""
    measured_by: str = ""

    def as_dict(self) -> dict:
        m = self.mutation
        return {"file": m.file, "line": m.line, "key": m.key, "kind": m.kind,
                "mutation": m.describe(), "status": self.status, "reason": self.reason,
                "measured_by": self.measured_by}


@dataclass
class SweepReport:
    results: list[SweepResult] = field(default_factory=list)
    cannot_verify: str = ""
    recovered: str = ""
    notes: list[str] = field(default_factory=list)
    selection: str = ""
    seconds: float = 0.0

    def _with(self, status: str) -> list[SweepResult]:
        return [r for r in self.results if r.status == status]

    @property
    def survivors(self) -> list[SweepResult]:
        # ⛔ Like Report.blocking: a cannot-verify sweep names no survivor it could trust.
        return [] if self.cannot_verify else self._with(judge.SURVIVED)

    def as_dict(self) -> dict:
        return {
            "ok": True,
            "mutations": len(self.results),
            "survivors": [r.as_dict() for r in self.survivors],
            "killed": len(self._with(judge.KILLED)),
            "unparseable": [r.as_dict() for r in self._with(UNPARSEABLE)],
            "not_applied": [r.as_dict() for r in self._with(NOT_APPLIED)],
            "invalid": [r.as_dict() for r in self._with(judge.INVALID)],
            "cannot_verify": self.cannot_verify, "recovered": self.recovered,
            "notes": list(self.notes), "selection": self.selection,
            "seconds": round(self.seconds, 1),
        }


# --- which lines changed --------------------------------------------------------------------


def is_test_path(rel: str) -> bool:
    """A test file is what a sweep's mutations are measured BY, never what they mutate."""
    p = PurePosixPath(rel)
    name = p.name
    return (p.parts[:1] == ("tests",) or name.startswith("test_") or name.endswith("_test.py")
            or ".test." in name or name == "conftest.py")


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True,
                          errors="replace", timeout=60)


def changed_lines(worktree: Path, base: str) -> tuple[dict[str, set[int]], str]:
    """``{file: {1-based line numbers}}`` the branch changed vs ``base`` — committed AND
    uncommitted edits (the working tree is diffed against the merge base), plus every line of
    an untracked, unignored file. Only sweepable source: ``.py``/``.js``/``.mjs``, never a test
    file. Returns ``(lines, error)``."""
    mb = _git(worktree, "merge-base", base, "HEAD")
    if mb.returncode != 0 or not mb.stdout.strip():
        return {}, (f"`{base}` does not resolve to a merge base with HEAD in {worktree} — "
                    f"{(mb.stderr or '').strip()[:200] or 'no output'}")
    base_sha = mb.stdout.strip()
    # Names come NUL-separated and unquoted (`-z`): a `+++ b/<name>` header is quoted for
    # non-ASCII/odd names and tab-suffixed for names with spaces, so it is never parsed.
    names = _git(worktree, "diff", "--name-only", "-z", "--no-renames", base_sha, "--")
    if names.returncode != 0:
        return {}, f"git diff failed: {(names.stderr or '').strip()[:200]}"
    out: dict[str, set[int]] = {}
    for rel in (n for n in names.stdout.split("\0") if n and _sweepable(n)):
        diff = _git(worktree, "diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames",
                    base_sha, "--", rel)
        if diff.returncode != 0:
            return {}, f"git diff failed: {(diff.stderr or '').strip()[:200]}"
        for raw in diff.stdout.splitlines():
            m = _RE_HUNK.match(raw)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or "1")
                if count:
                    out.setdefault(rel, set()).update(range(start, start + count))
    untracked = _git(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    if untracked.returncode != 0:
        return {}, f"git ls-files failed: {(untracked.stderr or '').strip()[:200]}"
    for rel in (n for n in untracked.stdout.split("\0") if n and _sweepable(n)):
        try:
            n = len((worktree / rel).read_text().splitlines())
        except (OSError, UnicodeDecodeError) as e:
            return {}, f"{rel} is a changed source file that could not be read: {e}"
        out[rel] = set(range(1, n + 1))
    return out, ""


def _sweepable(rel: str) -> bool:
    return rel.endswith(PY_SUFFIXES + JS_SUFFIXES) and not is_test_path(rel)


# --- what to mutate -------------------------------------------------------------------------


def _splice(line: str, col: int, end: int, new: str) -> str:
    """Replace the UTF-8 BYTE span ``[col, end)`` of ``line`` (ast offsets are bytes)."""
    b = line.encode()
    return (b[:col] + new.encode() + b[end:]).decode()


def _seg(line: str, col: int, end: int) -> str:
    return line.encode()[col:end].decode()


def _single_line(node: ast.AST) -> bool:
    return getattr(node, "lineno", None) == getattr(node, "end_lineno", -1)


def _own_returns(fn: ast.AST) -> list[ast.Return]:
    """The ``return`` statements that belong to ``fn`` itself, not to a nested def/lambda."""
    found: list[ast.Return] = []
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Return):
            found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _is_none(value: ast.AST | None) -> bool:
    return value is None or (isinstance(value, ast.Constant) and value.value is None)


def _is_code(node: ast.AST | None) -> bool:
    return (isinstance(node, ast.Constant) and type(node.value) is int
            and node.value in (0, 1))


def py_mutations(rel: str, text: str, lines: set[int]) -> list[Mutation]:
    """Every mechanical mutation of the CHANGED lines of one Python file."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    src = text.split("\n")
    muts: list[Mutation] = []

    def add(node: ast.AST, kind: str, col: int, end: int, new: str) -> None:
        ln = node.lineno
        line = src[ln - 1]
        after = _splice(line, col, end, new)
        if after != line:
            muts.append(Mutation(rel, ln, kind, line, after))

    for node in ast.walk(tree):
        if isinstance(node, ast.If) and node.test.lineno in lines and _single_line(node.test):
            t = node.test
            seg = _seg(src[t.lineno - 1], t.col_offset, t.end_col_offset)
            add(t, "if-false", t.col_offset, t.end_col_offset, f"False and ({seg})")
            if node.orelse:
                add(t, "if-true", t.col_offset, t.end_col_offset, f"True or ({seg})")
        elif (isinstance(node, ast.Compare) and node.lineno in lines and _single_line(node)):
            line = src[node.lineno - 1]
            left = node.left
            for op, right in zip(node.ops, node.comparators):
                flip = _PY_FLIP.get(type(op))
                if flip and _single_line(left) and _single_line(right):
                    between = _seg(line, left.end_col_offset, right.col_offset)
                    if between.strip(" ()\t") == flip[0]:
                        add(node, "compare-flip", left.end_col_offset, right.col_offset,
                            between.replace(flip[0], flip[1], 1))
                left = right
        elif (isinstance(node, ast.Return) and node.lineno in lines and _single_line(node)
              and _is_code(node.value)):
            v = node.value
            add(node, "return-code", v.col_offset, v.end_col_offset, str(1 - v.value))
        elif (isinstance(node, ast.Call) and node.lineno in lines and _single_line(node)
              and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
              == "CompletedProcess"):
            code = node.args[1] if len(node.args) > 1 else next(
                (k.value for k in node.keywords if k.arg == "returncode"), None)
            if _is_code(code):
                add(code, "returncode", code.col_offset, code.end_col_offset,
                    str(1 - code.value))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rets = sorted((r for r in _own_returns(node) if _single_line(r)), key=lambda r: r.lineno)
            nones = [r for r in rets if _is_none(r.value)]
            values = [r for r in rets if not _is_none(r.value)]
            if not (nones and values):
                continue
            for r in values:
                if r.lineno in lines:
                    v = r.value
                    add(r, "return-none", v.col_offset, v.end_col_offset, "None")
            expr = _seg(src[values[0].lineno - 1], values[0].value.col_offset,
                        values[0].value.end_col_offset)
            for r in nones:
                if r.lineno in lines:
                    add(r, "return-value", r.col_offset, r.end_col_offset, f"return {expr}")
    return sorted(muts, key=lambda m: (m.line, m.kind, m.after))


def _js_code_end(line: str) -> int:
    """Where code stops on this line: the start of a ``//`` comment outside any string."""
    quote = ""
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
        elif line.startswith("//", i):
            return i
        i += 1
    return len(line)


def _in_js_string(line: str, pos: int) -> bool:
    quote = ""
    i = 0
    while i < pos:
        c = line[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
        i += 1
    return bool(quote)


def _js_has_else(src: list[str], idx: int, after: int) -> bool:
    """Does the ``if`` on ``src[idx]`` have an ``else``? Line-based: ``} else`` later on the
    same line, or the next non-blank line at the SAME indent is an ``else``/``} else``."""
    line = src[idx]
    if re.search(r"\}\s*else\b", line[after:]):
        return True
    indent = len(line) - len(line.lstrip())
    for nxt in src[idx + 1:]:
        stripped = nxt.strip()
        if not stripped:
            continue
        ind = len(nxt) - len(nxt.lstrip())
        if ind < indent:
            return False
        if ind == indent:
            return bool(re.match(r"(\}\s*)?else\b", stripped))
    return False


def js_mutations(rel: str, text: str, lines: set[int]) -> list[Mutation]:
    """Every mechanical mutation of the CHANGED lines of one JS file. Line-based (no JS
    parser): a mutation inside something it misread is caught by ``node --check`` and
    reported UNPARSEABLE, never counted as killed."""
    src = text.split("\n")
    muts: list[Mutation] = []
    for ln in sorted(lines):
        if ln > len(src):
            continue
        line = src[ln - 1]
        stripped = line.lstrip()
        if stripped.startswith(("//", "*", "/*")):
            continue
        code_end = _js_code_end(line)
        for m in _RE_JS_IF.finditer(line, 0, code_end):
            if _in_js_string(line, m.start()):
                continue
            open_at = m.end() - 1
            depth = 0
            close_at = -1
            for i in range(open_at, code_end):
                if line[i] == "(":
                    depth += 1
                elif line[i] == ")":
                    depth -= 1
                    if depth == 0:
                        close_at = i
                        break
            if close_at < 0:
                continue        # a multi-line condition — not a one-line mutation
            cond = line[open_at + 1:close_at]
            muts.append(Mutation(rel, ln, "if-false", line,
                                 f"{line[:open_at + 1]}false && ({cond}){line[close_at:]}"))
            if _js_has_else(src, ln - 1, close_at):
                muts.append(Mutation(rel, ln, "if-true", line,
                                     f"{line[:open_at + 1]}true || ({cond}){line[close_at:]}"))
        for m in _RE_JS_CMP.finditer(line, 0, code_end):
            if _in_js_string(line, m.start()):
                continue
            op = m.group(1)
            muts.append(Mutation(rel, ln, "compare-flip", line,
                                 f"{line[:m.start()]}{_JS_FLIP[op]}{line[m.end():]}"))
        for m in _RE_JS_RET.finditer(line, 0, code_end):
            if _in_js_string(line, m.start()):
                continue
            new = "1" if m.group(1) == "0" else "0"
            muts.append(Mutation(rel, ln, "return-code", line,
                                 f"{line[:m.start(1)]}{new}{line[m.end(1):]}"))
    return muts


def collect_mutations(worktree: Path, base: str) -> tuple[list[Mutation], str]:
    changed, err = changed_lines(worktree, base)
    if err:
        return [], err
    muts: list[Mutation] = []
    for rel in sorted(changed):
        try:
            text = (worktree / rel).read_text()
        except (OSError, UnicodeDecodeError) as e:
            # ⛔ fail CLOSED: a changed source file we cannot read is not "nothing to sweep".
            return [], f"{rel} is a changed source file that could not be read: {e}"
        if rel.endswith(PY_SUFFIXES):
            try:
                ast.parse(text)
            except (SyntaxError, ValueError) as e:
                return [], f"{rel} is a changed source file that could not be parsed: {e}"
        gen = py_mutations if rel.endswith(PY_SUFFIXES) else js_mutations
        muts.extend(gen(rel, text, changed[rel]))
    return muts, ""


def _anchor(text: str, mut: Mutation) -> tuple[str, str] | None:
    """Widen the mutated line to a span that occurs EXACTLY ONCE in ``text`` (the judge's
    :func:`~chela.judge.apply_mutation` refuses anything else), with the matching mutated
    span. ``None`` when the line on disk is not the one the mutation was generated from."""
    src = text.split("\n")
    i = mut.line - 1
    if i >= len(src) or src[i] != mut.before:
        return None
    lo = hi = i
    while True:
        before = "\n".join(src[lo:hi + 1])
        # A leading/trailing newline pins the span to WHOLE lines — `    if x:` is otherwise
        # a substring of `        if x:`.
        pre = "\n" if lo > 0 else ""
        post = "\n" if hi < len(src) - 1 else ""
        span = pre + before + post
        if text.count(span) == 1:
            mutated = src[lo:hi + 1]
            mutated[i - lo] = mut.after
            return span, pre + "\n".join(mutated) + post
        # Terminates: once the span is the whole file it occurs exactly once.
        lo, hi = max(0, lo - 1), min(len(src) - 1, hi + 1)


# --- the recovery marker --------------------------------------------------------------------


def marker_path(worktree: Path) -> Path:
    return Path(worktree) / RECOVERY_MARKER


def _write_marker(worktree: Path, rel: str, original: str, mutated: str) -> None:
    path = marker_path(worktree)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "file": rel, "original": original,
                               "mutated": mutated}))
    os.replace(tmp, path)


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recover(worktree: Path) -> tuple[str, str]:
    """Undo a mutation a killed sweep left on disk. Returns ``(done, error)``: ``done`` says
    what was restored (``""`` if nothing was needed); ``error`` means the sweep must NOT run —
    another sweep is live, or the file moved on since and nobody can say what is safe."""
    path = marker_path(worktree)
    if not path.exists():
        return "", ""
    try:
        data = json.loads(path.read_text())
        rel, original, mutated = data["file"], data["original"], data["mutated"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        return "", f"{path} is unreadable ({e}) — inspect it, restore by hand, then delete it"
    if _pid_alive(data.get("pid")):
        return "", (f"another sweep (pid {data['pid']}) is mutating {rel} in this worktree "
                    "right now — wait for it to finish")
    target = Path(worktree) / rel
    try:
        current = target.read_text()
    except (OSError, UnicodeDecodeError) as e:
        return "", f"{rel} could not be read to recover it: {e}"
    if current == original:
        path.unlink(missing_ok=True)
        return "", ""
    if current != mutated:
        return "", (f"{path} says a killed sweep left {rel} mutated, but {rel} is now neither "
                    "the mutation nor the original — it was edited since. Restore it by hand "
                    "(the original is in the marker's `original` field), then delete the marker")
    target.write_text(original)
    if target.read_text() != original:
        return "", f"{rel} could not be restored from {path}"
    path.unlink(missing_ok=True)
    return f"restored {rel} from a killed sweep's recovery marker", ""


class _Interrupted(BaseException):
    """SIGTERM/SIGHUP during a sweep — raised so the ``finally`` that restores the file runs."""


def _install_signal_handlers() -> dict:
    """Off the main thread ``signal.signal`` raises ValueError — no handlers, nothing to undo."""
    def _raise(signum, _frame):
        raise _Interrupted(signum)

    old = {}
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            old[sig] = signal.signal(sig, _raise)
        except (OSError, ValueError):
            pass
    return old


def _restore_signal_handlers(old: dict) -> None:
    for sig, handler in old.items():
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError):
            pass


# --- the sweep ------------------------------------------------------------------------------


def sweep(
    worktree: str | Path, base: str, test_cmd: str, *,
    timeout: float = judge.SUITE_TIMEOUT_SECONDS, select_tests: bool = True, progress=None,
) -> SweepReport:
    worktree = Path(worktree).resolve()
    started = time.monotonic()
    old = _install_signal_handlers()
    try:
        report = _sweep(worktree, base, test_cmd, timeout=timeout, select_tests=select_tests,
                        progress=progress)
    except _Interrupted as e:
        # The mutation's own `finally` has already restored the file by the time this runs.
        report = SweepReport(cannot_verify=f"interrupted by signal {e.args[0]} — the sweep "
                                           "stopped; the mutated file was restored")
    finally:
        _restore_signal_handlers(old)
    report.seconds = time.monotonic() - started
    return report


def _sweep(worktree: Path, base: str, test_cmd: str, *, timeout: float, select_tests: bool,
           progress) -> SweepReport:
    report = SweepReport()
    report.recovered, err = recover(worktree)
    if err:
        report.cannot_verify = err
        return report
    muts, err = collect_mutations(worktree, base)
    if err:
        report.cannot_verify = err
        return report
    if not muts:
        report.notes.append(f"no changed conditional, comparison or return code vs `{base}` — "
                            "nothing to sweep")
        return report

    env_problem = judge.provision_suite_env(worktree)
    if env_problem:
        report.cannot_verify = f"the worktree could not be PROVISIONED to run the suite: {env_problem}"
        return report
    with tempfile.TemporaryDirectory(prefix="chela-sweep-select-") as scratch:
        baseline, selector, report.selection = judge.run_baseline(
            worktree, test_cmd, timeout, Path(scratch), select_tests=select_tests)
    if not baseline.green:
        why = baseline.detail or judge._last_meaningful_line(baseline.tail)
        report.cannot_verify = (
            f"the suite is NOT GREEN before any mutation (`{test_cmd}` exited "
            f"{baseline.exit_code}{': ' + why if why else ''}) — a red baseline measures "
            "nothing. Fix the suite first.")
        return report

    for n, mut in enumerate(muts):
        if progress is not None:
            progress(n, len(muts), mut)
        result, contamination = _run_one(worktree, test_cmd, mut, baseline, timeout, selector)
        report.results.append(result)
        if contamination:
            report.cannot_verify = contamination
            return report
    return report


def _run_one(worktree: Path, test_cmd: str, mut: Mutation, baseline, timeout: float,
             selector) -> tuple[SweepResult, str]:
    path = worktree / mut.file
    try:
        original = path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        return SweepResult(mut, NOT_APPLIED, f"could not read {mut.file}: {e}"), ""
    anchored = _anchor(original, mut)
    if anchored is None:
        return SweepResult(mut, NOT_APPLIED, "the line could not be anchored uniquely"), ""
    before, after = anchored
    _write_marker(worktree, mut.file, original, original.replace(before, after, 1))
    try:
        result = _apply_and_measure(worktree, test_cmd, mut, before, after, baseline, timeout,
                                    selector)
    finally:
        # ⛔ ALWAYS — and prove it. A mutation left on disk is a corruption of the agent's
        # work. On SIGTERM this runs too (see `_Interrupted`); on SIGKILL the marker remains.
        restored = _restore(path, original)
        if restored:
            marker_path(worktree).unlink(missing_ok=True)
    if not restored:
        # The marker stays: it holds the only known-good copy of the original.
        return result, (f"{mut.file} could NOT be restored after its mutation — the original "
                        f"is in {marker_path(worktree)}. Nothing after this point was measured.")
    return result, ""


def _restore(path: Path, original: str) -> bool:
    try:
        if not path.exists() or path.read_text() != original:
            path.write_text(original)
        return path.read_text() == original
    except (OSError, UnicodeDecodeError):
        return False


def _apply_and_measure(worktree: Path, test_cmd: str, mut: Mutation, before: str, after: str,
                       baseline, timeout: float, selector) -> SweepResult:
    path = worktree / mut.file
    applied, reason, _ = judge.apply_mutation(path, before, after)
    if not applied:
        return SweepResult(mut, NOT_APPLIED, reason)
    parses, detail = judge.parse_check(path)
    if not parses:
        return SweepResult(mut, UNPARSEABLE, detail)
    exp = judge.Experiment(guard=mut.describe(), file=mut.file, before=before, after=after)
    outcome = judge._measure(worktree, test_cmd, exp, reason, parses, detail, baseline,
                             timeout, selector)
    return SweepResult(mut, outcome.verdict, outcome.reason, outcome.measured_by)


# --- `--sweep-equivalent` ---------------------------------------------------------------------

_RE_EQUIV = re.compile(r"^\s*(?P<file>[^\s:][^:]*):(?P<line>\d+)\s*:\s*(?P<why>.*?)\s*$", re.S)


def parse_equivalents(notes: list[str]) -> tuple[list[dict], str]:
    """``'<file:line>: <why no outcome can change>'`` → ``[{key, file, line, why}]``. A note
    with no reason is refused: the reason IS the record."""
    out: list[dict] = []
    for note in notes or []:
        m = _RE_EQUIV.match(note or "")
        if not m:
            return [], f"--sweep-equivalent {note!r} is not '<file:line>: <why>'"
        if not m.group("why"):
            return [], (f"--sweep-equivalent {note!r} gives no reason — say why no outcome "
                        "can change under the mutation")
        file, line = m.group("file").strip(), int(m.group("line"))
        out.append({"key": f"{file}:{line}", "file": file, "line": line, "why": m.group("why")})
    return out, ""


def check_equivalents(survivors: list[dict], equivalents: list[dict]) -> tuple[list[dict], list[dict]]:
    """``(uncovered survivors, equivalents naming no survivor)`` — both must be empty."""
    keys = {s["key"] for s in survivors}
    named = {e["key"] for e in equivalents}
    return ([s for s in survivors if s["key"] not in named],
            [e for e in equivalents if e["key"] not in keys])


def render_equivalents(equivalents: list[dict]) -> str:
    """The markdown block shown to the judge and appended to the PR body."""
    if not equivalents:
        return ""
    lines = ["### ⚖️🧹 `chela judge sweep` survivors declared equivalent by the agent", "",
             "Each mutation below SURVIVED the suite; the agent says no outcome can change "
             "under it. Not verified by chela — check the reason.", ""]
    for e in equivalents:
        muts = "; ".join(_one_line(m) for m in e.get("mutations") or [])
        lines.append(f"- `{_one_line(e['key'])}` — {_one_line(e['why'])}"
                     + (f" (mutation: {muts})" if muts else ""))
    return "\n".join(lines)


def _one_line(text: object) -> str:
    """Agent-authored text bound for ONE markdown bullet: a CR/LF would start a forged one."""
    return re.sub(r"\s*[\r\n]+\s*", " ", str(text))


def run_sweep(
    worktree: str | Path, *, base: str | None = None, workflow_path: str | Path | None = None,
    test_cmd: str | None = None, timeout: float | None = None, progress=None,
) -> dict:
    """``chela judge sweep``'s entry point: resolve the suite (``--test-cmd`` or the
    workflow's ``judge.test_cmd``, the suite the judge measures against) and the base
    (``--base``, else ``origin/<workspace.base_branch>``, else ``origin/dev``), sweep, and
    return a JSON-friendly dict."""
    cmd = test_cmd.strip() if isinstance(test_cmd, str) and test_cmd.strip() else None
    to, select = timeout, True
    if workflow_path:
        from chela import workflow as workflow_mod

        try:
            wf = workflow_mod.load_workflow(workflow_path)
        except Exception as e:      # a WORKFLOW.md that does not parse
            return {"ok": False, "error": f"{workflow_path} could not be read: {e}"}
        cfg = judge.judge_suite_config(wf)
        cmd = cmd or cfg.test_cmd
        to = to if to is not None else cfg.suite_timeout_seconds
        select = cfg.select_tests
        if base is None:
            base = f"origin/{wf.get('workspace', 'base_branch', default='dev')}"
    if not cmd:
        return {"ok": False, "error": "no suite to run — pass --test-cmd, or --workflow "
                                      "pointing at a WORKFLOW.md with `judge.test_cmd` set"}
    report = sweep(worktree, base or "origin/dev", cmd,
                   timeout=to if to is not None else judge.SUITE_TIMEOUT_SECONDS,
                   select_tests=select, progress=progress)
    return report.as_dict()
