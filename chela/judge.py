"""⚖️ THE JUDGE — adversarial review whose BLOCKING verdicts are FACTS, not opinions.

THE MEASUREMENT THAT MADE THIS URGENT (2026-07-14). Five dispatched PRs reached
``awaiting_review``, all five CI-green. Five hand-spawned adversarial reviewers sent FOUR of
them back — and not one for a broken feature. Every feature worked. They went back because
**the thing meant to PROVE the feature works could not fail**: a sidebar guard that still
passed with the guarded state folded back in; a colourblind cue whose glyph could be emptied
with `0 failures`; a lazy-bind PR whose entire production wiring could be reverted with
`1112 passed`; a gate fix whose two thread starts could both be deleted, `100% GREEN`.
⛔ CI cannot catch this class: CI runs the tests, and the tests are the thing that is broken.

⛔ THE DESIGN IS NOT "SPAWN A REVIEWER AND TRUST IT."

The reviewing agent decides NOTHING. It proposes EXPERIMENTS — ``(file, before, after)`` —
and this module executes them: it applies the mutation itself, reads the file back to prove
it changed, parses it to prove it still loads, runs the repo's OWN suite, restores the file,
and adjudicates the result. The agent's opinion never enters the blocking path; only the
exit code of a suite chela ran itself does. That is what makes a blocking verdict a fact,
in exactly the sense CMX-69's CI gate is a fact: *a failing check is not a judgment.*

THE THREE MECHANICAL FINDINGS, and they are the ONLY things allowed to block:

* **MUTATION** — corrupt a guard the PR claims to add. Suite still green ⇒ the guard is not
  a guard. BLOCK.
* **WIRING** — revert the production call-site. Suite still green ⇒ the tests never exercise
  what runs. BLOCK. (Mechanically identical to a mutation; the distinction is what was
  chosen, not how it is adjudicated.)
* **CLAIM** — a number in the PR body that does not reproduce. Not implemented here as a
  separate path: a claim about the suite IS the baseline this module runs, and the baseline
  is reported verbatim.

Everything else — style, taste, "I'd have done it differently" — is a NOTE. Notes are posted
as a PR comment and can never send a run back. **The judge is allowed to be useless. It is
not allowed to be wrong**: a wrong ``changes_requested`` burns a rework round and can push a
good PR to ``needs_human``.

🔴 A MUTATION IS ITSELF AN ARTIFACT, AND IT CAN LIE. Both of these were made BY HAND on PR
#85 on 2026-07-14, and either would have produced a confidently wrong verdict:

* **THE MUTATION THAT NEVER APPLIED.** A `sed` whose delimiter collided with a `|` in the
  target line left the file UNCHANGED. The suite stayed green — and a naive judge reads that
  as "the guard is broken → BLOCK", blocking a good PR on a mutation it never made. ⇒
  :func:`apply_mutation` refuses an anchor that does not occur EXACTLY ONCE, and reads the
  file BACK FROM DISK to prove the text really moved. An unapplied mutation is INVALID and
  cannot block.
* **THE MUTATION THAT BROKE THE PARSE.** Deleting an `if (...) {` unbalanced the braces →
  syntax error → 22 failures. That is red for the WRONG reason: it proves nothing about the
  guard, and a naive judge reads it as "the guard fired → PASS". ⇒ :func:`parse_check` runs
  after every mutation, and a red run whose test count collapsed is INVALID, not evidence.

⛔ THE JUDGE NEVER MERGES. It may send a run back (through the existing carrier —
``dispatcher.request_changes``, never a second path) or leave it exactly where it was. A
clean run stays in ``awaiting_review`` for the orchestrator, who owns the merge.

⛔ UNKNOWN IS NEVER A PASS — AND NEVER A FAIL EITHER. A red baseline, a dirty worktree, a
suite that would not run, zero proposed experiments: all of them are CANNOT VERIFY. Nothing
is blocked and nothing is approved; the run is handed to a human, exactly as
``CI_UNKNOWN`` is.

⚖️🕳️ A BLOCKING VERDICT IS POSTED TO THE PR UNCONDITIONALLY — CMX-228. It used to reach the
PR only from inside ``dispatcher.request_changes``, past that function's own
``status == 'awaiting_review'`` check and compare-and-swap — both of which return early,
posting NOTHING, the moment the run moves out from under a still-running judge (a human
merges it, or the CI gate reaches it first). A clean verdict has always posted with no such
gate. The result was inverted severity: the finding that mattered most — a guard that
SURVIVED deliberate corruption — was the one most likely to go unpublished, precisely
because it takes longer to compute than a clean pass and so has more time to race a merge.
:func:`judge_run` now posts the block-body comment first, before either check; the CAS in
``request_changes`` still guards the run ROW (no resurrecting an already-merged run), it
just never again gates whether the finding is SHOWN.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

# --- verdicts, per experiment ------------------------------------------------
#
# SURVIVED is the ONLY one that blocks. The other two are the reason it is safe to.
SURVIVED = "SURVIVED"   # applied, parses, suite STILL GREEN ⇒ the guard is not a guard. FACT.
KILLED = "KILLED"       # the suite went red the way a firing guard makes it go red. Good PR.
INVALID = "INVALID"     # the experiment proved NOTHING. ⛔ It never blocks and never absolves.

# --- the run-row judge states (dispatcher.runs.judge_state) ------------------
J_RUNNING = "running"
J_CLEAN = "clean"
J_BLOCKED = "blocked"
J_CANNOT_VERIFY = "cannot_verify"
# ⚖️🧊 CMX-239: a guard SURVIVED corruption, but `request_changes`'s CAS refused to record
# it (the run moved out of `awaiting_review` while the judge was still working — a human
# merged it, or the CI gate got there first). Deliberately its OWN value, never `J_BLOCKED`:
# `J_BLOCKED` also persists on a row long after it settles (through rework rounds, even
# through an eventual `needs_human` escalation — see `inbox.run_events`'s guard test), so
# reusing it here would make a LATER, unrelated status change on an ordinary blocked run
# misread as this race. This value means exactly one thing and is set from exactly one
# place: the finding is real, and the run row never moved to reflect it.
J_BLOCKED_RACE = "blocked_race"

# A judge that proposes forty mutations is not being thorough, it is re-running the suite
# forty times. The cap is enforced OUT LOUD (the report says what was dropped) — a silent
# truncation reads as "everything was checked" when it was not.
MAX_EXPERIMENTS = 12

# Each experiment re-runs the whole suite, so the timeout is per suite run, not per judge.
SUITE_TIMEOUT_SECONDS = 900

# The consistency check on a RED mutated run: a guard firing flips some passes into
# failures, so `passed + failed` stays roughly constant. A file that no longer LOADS takes
# whole modules down with it, and the number of tests that ran collapses. Below this
# fraction of the baseline's, the red is not a guard firing — it is a broken artifact, and
# it is INVALID.
MIN_RAN_RATIO = 0.9

_PY_SUFFIXES = (".py",)
_JS_SUFFIXES = (".js", ".mjs", ".cjs")

# pytest ("3 failed, 1108 passed, 2 errors in 41s") and node --test ("# pass 15" /
# "# fail 0") in one pass — a repo's `judge.test_cmd` may well run both.
_RE_PASSED = re.compile(r"(\d+) passed\b")
_RE_FAILED = re.compile(r"(\d+) failed\b")
_RE_ERRORS = re.compile(r"(\d+) errors?\b")
_RE_NODE_PASS = re.compile(r"^# pass (\d+)$", re.M)
_RE_NODE_FAIL = re.compile(r"^# fail (\d+)$", re.M)

# ⛔ CMX-177: a count is not a cause. pytest's short summary line ("FAILED path::test - why")
# and node --test's TAP line ("not ok N - test name") are the only place either format names
# WHICH test failed, and they are what turns "1 failed" into something a human can act on.
_RE_PYTEST_FAILED_NAME = re.compile(r"^FAILED (\S+)", re.M)
_RE_NODE_FAILED_NAME = re.compile(r"^not ok \d+ - (.+)$", re.M)

# ⛔ CMX-177 rework: both patterns above anchor to the START of the line (`^FAILED`,
# `^not ok`), but a coloured runner puts an SGR escape THERE instead — pytest's own summary
# line is `\x1b[31mFAILED\x1b[0m path::test - why`, which never matches `^FAILED` at all.
# `findall` then returns [], `named` is empty, and the report silently falls back to the
# bare-count message this feature exists to replace. Observed live: the judge daemon's own
# environment carries `FORCE_COLOR=3`, so this is not a hypothetical — it is the box the
# judge actually runs on. Strip before matching, once, here — not at every call site, so a
# future caller can't forget it.
_RE_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _RE_ANSI_ESCAPE.sub("", text)

MAX_NAMED_FAILURES = 5

SUITE_TAIL_CHARS = 3000


class SuiteResult(NamedTuple):
    """What the repo's own test command did. ``ok`` is "the process ran", not "it passed"."""
    ok: bool
    exit_code: int
    passed: int
    failed: int
    errors: int
    tail: str
    detail: str = ""

    @property
    def green(self) -> bool:
        return self.ok and self.exit_code == 0

    @property
    def ran(self) -> int:
        """Tests that actually executed. The collapse of this number is what unmasks a
        mutation that took the suite down instead of tripping a guard."""
        return self.passed + self.failed

    def as_dict(self) -> dict:
        return {"ok": self.ok, "exit_code": self.exit_code, "passed": self.passed,
                "failed": self.failed, "errors": self.errors, "detail": self.detail}


@dataclass
class Experiment:
    """One proposed corruption. The agent writes these; it does not run them."""
    guard: str
    file: str
    before: str
    after: str
    kind: str = "mutation"       # "mutation" | "wiring" — adjudicated identically

    @classmethod
    def parse(cls, raw: object) -> tuple["Experiment | None", str]:
        if not isinstance(raw, dict):
            return None, "not a JSON object"
        vals = {}
        for key in ("guard", "file", "before", "after"):
            v = raw.get(key)
            if not isinstance(v, str) or not v.strip():
                return None, f"missing or empty {key!r}"
            vals[key] = v
        kind = raw.get("kind")
        kind = kind if kind in ("mutation", "wiring") else "mutation"
        return cls(guard=vals["guard"].strip(), file=vals["file"].strip(),
                   before=vals["before"], after=vals["after"], kind=kind), ""


@dataclass
class Outcome:
    """The adjudication of one experiment. ``verdict`` is decided here and nowhere else."""
    experiment: Experiment
    verdict: str
    reason: str
    baseline: SuiteResult | None = None
    mutated: SuiteResult | None = None
    parse_detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.verdict == SURVIVED

    def as_dict(self) -> dict:
        return {
            "guard": self.experiment.guard, "file": self.experiment.file,
            "kind": self.experiment.kind, "verdict": self.verdict, "reason": self.reason,
            "parse": self.parse_detail,
            "mutated": self.mutated.as_dict() if self.mutated else None,
        }


@dataclass
class Report:
    """Everything the judge found — and, when it found nothing it could trust, why."""
    outcomes: list[Outcome] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    baseline: SuiteResult | None = None
    cannot_verify: str = ""     # non-empty ⇒ NOTHING here may block. Unknown is not a fail.
    dropped: int = 0            # experiments past MAX_EXPERIMENTS — said out loud, never silent

    @property
    def blocking(self) -> list[Outcome]:
        # ⛔ The single choke point. A cannot-verify report blocks NOTHING, whatever its
        # outcomes look like — a mutation run against a suite that was already red, or in a
        # worktree with unknown edits in it, is not evidence of anything.
        if self.cannot_verify:
            return []
        return [o for o in self.outcomes if o.blocking]

    @property
    def state(self) -> str:
        if self.blocking:
            return J_BLOCKED
        if self.cannot_verify:
            return J_CANNOT_VERIFY
        return J_CLEAN


# --- the mechanics: apply, parse, run ----------------------------------------


def apply_mutation(path: Path, before: str, after: str) -> tuple[bool, str, str | None]:
    """Apply one mutation, and PROVE it landed. Returns ``(applied, reason, original_text)``.

    ⛔ Every refusal here is a mutation that would otherwise have produced a confidently
    wrong verdict — a green suite that was green because NOTHING WAS CHANGED reads exactly
    like a guard that does not work:

    * the anchor is not in the file → the mutation never applied (the `sed`-delimiter trap);
    * the anchor occurs more than once → which one moved? An ambiguous edit is not evidence;
    * ``before == after`` → a no-op dressed as an experiment;
    * the file on disk did not change, or does not contain the replacement → whatever the
      write did, it did not do this.

    The original text comes back so the caller can restore it — the caller MUST, in a
    ``finally``: a mutation left on disk is a corruption of the artifact under test.
    """
    if before == after:
        return False, "before == after: this mutation changes nothing", None
    if not path.is_file():
        return False, f"{path} is not a file in the judge worktree", None
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        return False, f"{path} could not be read: {e}", None
    occurrences = text.count(before)
    if occurrences == 0:
        return False, (
            "the `before` anchor does not occur in the file — THE MUTATION NEVER APPLIED, "
            "and a suite that stayed green under a mutation that was never made proves "
            "nothing"
        ), None
    if occurrences > 1:
        return False, (
            f"the `before` anchor occurs {occurrences} times — an ambiguous mutation is not "
            "evidence; anchor it on a unique span"
        ), None
    try:
        path.write_text(text.replace(before, after, 1))
        back = path.read_text()
    except OSError as e:
        return False, f"{path} could not be written: {e}", text
    # ⛔ Read it BACK FROM DISK. Not "we called write_text and it did not raise" — the whole
    # bug class here is an artifact that is not what you think it is.
    if back == text:
        return False, "the file on disk is UNCHANGED after the write — the mutation did not apply", text
    if after not in back:
        return False, "the file changed, but the intended replacement is not in it", text
    return True, "applied (the file on disk really changed)", text


def parse_check(path: Path) -> tuple[bool, str]:
    """Does the mutated file still PARSE? Returns ``(ok, detail)``.

    ⛔ A mutation that breaks the syntax makes the suite red for the WRONG reason: it proves
    nothing about the guard, and a naive judge reads that red as "the guard fired → the PR is
    fine". This is what turns such a run into :data:`INVALID`.

    A file type we cannot parse-check (json, markdown, a template) is reported as unchecked
    rather than silently assumed good — it does not affect the blocking direction, which
    requires a GREEN suite and therefore a file that demonstrably loaded.
    """
    suffix = path.suffix.lower()
    if suffix in _PY_SUFFIXES:
        try:
            compile(path.read_text(), str(path), "exec")
        except SyntaxError as e:
            return False, f"the mutated python does not parse: {e}"
        except (OSError, ValueError) as e:
            return False, f"the mutated file could not be parse-checked: {e}"
        return True, "the mutated python still parses"
    if suffix in _JS_SUFFIXES:
        try:
            out = subprocess.run(
                ["node", "--check", str(path)],
                capture_output=True, text=True, errors="replace", timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return True, f"no parse check ran (node unavailable: {e})"
        if out.returncode != 0:
            return False, f"the mutated JS does not parse: {(out.stderr or '').strip()[:200]}"
        return True, "the mutated JS still parses (node --check)"
    return True, f"no parse check exists for {suffix or 'this file type'} — not parse-verified"


def _counts(text: str) -> tuple[int, int, int]:
    """(passed, failed, errors), summed across pytest's summary and node --test's."""
    passed = sum(int(m) for m in _RE_PASSED.findall(text)) + \
        sum(int(m) for m in _RE_NODE_PASS.findall(text))
    failed = sum(int(m) for m in _RE_FAILED.findall(text)) + \
        sum(int(m) for m in _RE_NODE_FAIL.findall(text))
    errors = sum(int(m) for m in _RE_ERRORS.findall(text))
    return passed, failed, errors


def _last_meaningful_line(tail: str, limit: int = 300) -> str:
    """The last line the suite actually printed — its summary, or whatever killed it."""
    for line in reversed((tail or "").splitlines()):
        line = line.strip()
        if line and not set(line) <= set("=-_ "):     # pytest's rules and separators say nothing
            return line[:limit]
    return ""


def _failing_test_names(tail: str, limit: int = MAX_NAMED_FAILURES) -> list[str]:
    """WHICH test(s) failed, not just how many. [] if the tail names none (truncated output,
    an unrecognized runner) — the caller falls back to the bare count, never invents a name."""
    plain = _strip_ansi(tail)
    names: list[str] = []
    for n in _RE_PYTEST_FAILED_NAME.findall(plain) + _RE_NODE_FAILED_NAME.findall(plain):
        n = n.strip()
        if n and n not in names:
            names.append(n)
    return names[:limit]


def _no_color_env() -> dict[str, str]:
    """The child's env, coloured output turned off. Hygiene alongside the ANSI strip in
    :func:`_failing_test_names` — that strip is the guarantee (it must survive coloured
    input from any producer, so it stays even with this), this is just why a human reading
    ``report.cannot_verify`` doesn't see raw escapes. The judge daemon's own environment has
    been observed running with ``FORCE_COLOR=3`` (inherited by whatever it spawns), so
    popping it — not merely leaving it — is what makes this effective on that box; setting
    only ``NO_COLOR`` and leaving ``FORCE_COLOR`` in place would still colour some runners
    that check ``FORCE_COLOR`` first.
    """
    env = dict(os.environ)
    env.pop("FORCE_COLOR", None)
    env["NO_COLOR"] = "1"
    env["PY_COLORS"] = "0"
    return env


def run_suite(test_cmd: str, cwd: Path, timeout: float = SUITE_TIMEOUT_SECONDS) -> SuiteResult:
    """Run the repo's OWN test command and read its exit code. Never raises.

    ⛔ The command comes from WORKFLOW.md (``judge.test_cmd``), never from the agent under
    judgment. A judge that got to pick its own suite could pick a narrow one that always
    passes — and a suite that cannot fail makes every mutation "survive", which is a
    false BLOCK on every PR. The one thing the judge may not choose is the thing it is
    judged against.

    GREEN IS THE EXIT CODE, not a parsed number. The counts are only ever used for the
    consistency check on a red run (did the suite still RUN?), where being approximate
    costs nothing.
    """
    try:
        out = subprocess.run(
            test_cmd, shell=True, cwd=str(cwd), env=_no_color_env(),
            capture_output=True, text=True, errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return SuiteResult(False, -1, 0, 0, 0, "", f"the suite did not finish in {timeout:.0f}s")
    except OSError as e:
        return SuiteResult(False, -1, 0, 0, 0, "", f"the suite could not be run: {e}")
    text = (out.stdout or "") + (out.stderr or "")
    passed, failed, errors = _counts(text)
    tail = text[-SUITE_TAIL_CHARS:] if len(text) > SUITE_TAIL_CHARS else text
    return SuiteResult(True, out.returncode, passed, failed, errors, tail)


def adjudicate(
    exp: Experiment,
    applied: bool,
    apply_reason: str,
    parses: bool,
    parse_detail: str,
    baseline: SuiteResult,
    mutated: SuiteResult | None,
) -> Outcome:
    """The one place a verdict is decided. Pure — every input is already a measurement.

    The order is the whole safety argument, and each refusal is a wrong verdict that was
    actually made by hand once:

    1. **not applied ⇒ INVALID.** The suite's greenness says nothing about a change that was
       never made. This is the trap that BLOCKS A GOOD PR, and it is the reason this
       function exists at all.
    2. **does not parse ⇒ INVALID.** Red for the wrong reason. It never blocks (it is not
       green) and it must not ABSOLVE either — a "guard fired" that was really a syntax
       error is a PR waved through on a broken artifact.
    3. **green ⇒ SURVIVED.** The mutation is live, the code loads, the suite ran, and it
       still passed. That is a FACT about the guard, not an opinion about the code.
    4. **red, but the suite stopped RUNNING ⇒ INVALID.** More errors than the baseline, or a
       collapsed test count: the mutation took the file down rather than tripping a guard.
    5. **red, consistently ⇒ KILLED.** The guard did its job.
    """
    if not applied:
        return Outcome(exp, INVALID, f"the mutation did not apply: {apply_reason}",
                       baseline, None, "")
    if not parses:
        return Outcome(
            exp, INVALID,
            f"the mutation broke the parse ({parse_detail}) — a suite that goes red because "
            "the file no longer loads proves NOTHING about the guard. Re-anchor it on a "
            "minimal, syntactically valid change (`if (false && …)`, an inverted comparison, "
            "an emptied return).",
            baseline, mutated, parse_detail,
        )
    if mutated is None or not mutated.ok:
        return Outcome(exp, INVALID,
                       f"the suite could not be run under the mutation: "
                       f"{mutated.detail if mutated else 'it never ran'}",
                       baseline, mutated, parse_detail)
    if mutated.exit_code == 0:
        return Outcome(
            exp, SURVIVED,
            "the mutation is live in the file, the file still parses, the suite RAN — and it "
            "still PASSED. The guard does not guard anything.",
            baseline, mutated, parse_detail,
        )
    if mutated.errors > baseline.errors:
        return Outcome(
            exp, INVALID,
            f"the suite went red with {mutated.errors} error(s) against the baseline's "
            f"{baseline.errors} — that is a file that no longer LOADS, not a guard firing. "
            "Not evidence.",
            baseline, mutated, parse_detail,
        )
    if baseline.ran and mutated.ran < baseline.ran * MIN_RAN_RATIO:
        return Outcome(
            exp, INVALID,
            f"only {mutated.ran} of the baseline's {baseline.ran} tests still RAN — the "
            "mutation took the suite down instead of tripping a guard. Not evidence.",
            baseline, mutated, parse_detail,
        )
    return Outcome(
        exp, KILLED,
        f"the suite went red under the mutation ({mutated.failed} failed of "
        f"{mutated.ran} run) — the guard fired.",
        baseline, mutated, parse_detail,
    )


def _git_dirty(worktree: Path) -> bool:
    """Are there uncommitted edits to TRACKED files? ⛔ Untracked junk does not count.

    The confound this guards against is an edit to the artifact under test — a stray
    modification that a mutation would then stack on top of, making the experiment measure
    two things at once. An UNTRACKED file is not that. And it must not count, because the
    judge's own machinery creates untracked files by running: the ``before_run`` hook builds
    a ``.venv``, and the BASELINE suite leaves ``__pycache__`` / ``.pytest_cache`` behind —
    so a bare ``git status --porcelain`` would call the worktree dirty from the second
    experiment onward and quietly report CANNOT VERIFY on every PR after the first.
    """
    out = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True, errors="replace",
    )
    return out.returncode != 0 or bool(out.stdout.strip())


def declared_npm_packages(worktree: Path) -> list[str]:
    """Every package ``package.json`` declares, dependencies and dev alike. [] if there is none."""
    pkg = worktree / "package.json"
    if not pkg.is_file():
        return []
    try:
        raw = json.loads(pkg.read_text())
    except (OSError, ValueError):
        return []
    names: list[str] = []
    for key in ("dependencies", "devDependencies"):
        block = raw.get(key)
        if isinstance(block, dict):
            names.extend(str(n) for n in block)
    return sorted(set(names))


def _unresolvable(worktree: Path, names: list[str]) -> list[str]:
    """The declared packages node could NOT resolve from this worktree.

    ``node_modules/<name>`` is the same thing ``tests/test_js_suites.py`` checks before it
    calls a DOM suite un-runnable, and matching that gate exactly is the point: this
    function must predict the suite's own verdict, not a stricter or looser one.
    """
    return [n for n in names if not (worktree / "node_modules" / Path(n)).is_dir()]


def _venv_python(worktree: Path) -> Path:
    """Where a ``uv``-managed ``.venv`` puts its interpreter, platform-appropriate."""
    if os.name == "nt":
        return worktree / ".venv" / "Scripts" / "python.exe"
    return worktree / ".venv" / "bin" / "python"


def _provision_python_env(worktree: Path, timeout: float = 600.0) -> str:
    """Make the judge worktree able to RUN a uv-managed Python suite. "" if it can already, or
    there is no ``pyproject.toml`` to provision for; the reason if it cannot be provisioned.

    ⛔ CMX-218. This module used to assume ``uv run`` re-syncs a missing ``.venv`` on its own,
    so nothing here provisioned Python at all — only a claim, never checked. It is false:
    live 2026-08-02 on cmx-217, a judge worktree with no ``.venv`` made
    ``uv run pytest`` exit 2 (``No such file or directory``) *before collecting a single
    test*, and a single ``uv sync`` — not a retry, not a wait — was the actual fix. Mirrors
    ``declared_npm_packages`` / the npm half below: provision in the JUDGED tree, because
    ``hooks.before_run`` in WORKFLOW.md builds worktrees out of the DAEMON's OLD copy, never
    the PR's (see the npm docstring below for the full argument).

    ``--all-extras``, matching ``hooks.before_run`` exactly: a bare ``uv sync`` (or the
    auto-sync a fresh ``uv run`` performs) drops every extra, and dashboard/telegram tests
    false-fail on a default-only sync (the CMX-21 trap).
    """
    if not (worktree / "pyproject.toml").is_file():
        return ""                       # not a uv-managed Python project — nothing to provision
    exe = _venv_python(worktree)
    if exe.is_file():
        return ""
    if not shutil.which("uv"):
        return (f"{worktree}/.venv is missing a Python interpreter and `uv` is not on this "
                "machine's PATH, so the judge could not provision it either")
    try:
        out = subprocess.run(
            ["uv", "sync", "--all-extras", "--quiet"],
            cwd=str(worktree), capture_output=True, text=True, errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"`uv sync` did not finish in {timeout:.0f}s in {worktree}"
    if out.returncode != 0:
        why = _last_meaningful_line((out.stdout or "") + (out.stderr or ""))
        return f"`uv sync` failed in {worktree} (exit {out.returncode}{': ' + why if why else ''})"
    if not exe.is_file():
        return (f"`uv sync` exited 0 in {worktree} but {exe} is STILL missing — the suite that "
                "needs it cannot run")
    log.info("judge: python env provisioned in %s (uv sync created .venv)", worktree)
    return ""


def provision_suite_env(worktree: Path, timeout: float = 600.0) -> str:
    """Make the judge worktree able to RUN the suite. "" if it can; the reason if it cannot.

    ⛔ CMX-80, and the reason the first fix did not work. THE JUDGE CANNOT RELY ON
    ``hooks.before_run`` TO BUILD ITS ENVIRONMENT. ``_launch_agent`` runs that hook out of
    the WorkflowDef the DAEMON loaded — ``runs.workflow_path``, the WORKFLOW.md at the REPO
    ROOT, on the default branch. It is NEVER the copy on the PR branch under judgment. So a
    PR whose whole content is "before_run must also run npm ci" is judged by a worktree
    built with the OLD before_run, watches its own DOM suites fail for want of jsdom, and
    reports CANNOT VERIFY on itself. A config fix cannot fix the thing that runs before the
    config is merged; only code in the judged tree can, and this is it.

    ⛔ CMX-218: this used to assume Python did not need this treatment, because ``uv run``
    "re-syncs the venv on every invocation." That is false — see
    :func:`_provision_python_env`, called first, below — and Python gets the exact same
    provision-in-the-judged-tree treatment npm already had.
    """
    python_problem = _provision_python_env(worktree, timeout)
    if python_problem:
        return python_problem

    names = declared_npm_packages(worktree)
    if not names:
        return ""                       # no npm deps declared — nothing to provision
    missing = _unresolvable(worktree, names)
    log.info("judge: suite env in %s: declared=%s missing=%s", worktree, names, missing or "none")
    if not missing:
        return ""

    if not (worktree / "package-lock.json").is_file():
        return (f"{', '.join(missing)} is not installed in {worktree}/node_modules and there "
                "is no package-lock.json to install it from")
    try:
        out = subprocess.run(
            ["npm", "ci", "--no-audit", "--no-fund", "--silent"],
            cwd=str(worktree), capture_output=True, text=True,
            errors="replace", timeout=timeout,
        )
    except FileNotFoundError:
        return (f"{', '.join(missing)} is not installed in {worktree}/node_modules and npm is "
                "not on this machine's PATH, so the judge could not install it either")
    except subprocess.TimeoutExpired:
        return f"`npm ci` did not finish in {timeout:.0f}s in {worktree}"
    if out.returncode != 0:
        why = _last_meaningful_line((out.stdout or "") + (out.stderr or ""))
        return f"`npm ci` failed in {worktree} (exit {out.returncode}{': ' + why if why else ''})"

    still = _unresolvable(worktree, names)
    if still:
        return (f"`npm ci` exited 0 in {worktree} but {', '.join(still)} is STILL not in "
                "node_modules — the suite that needs it cannot run")
    log.info("judge: suite env provisioned in %s (npm ci installed %s)", worktree, missing)
    return ""


def _diagnose_red_baseline(
    worktree: Path, test_cmd: str, base_branch: str, timeout: float,
) -> str:
    """WHY the baseline is red: this branch's own doing, already broken on ``base_branch``, or
    undeterminable. One sentence, always — never blank.

    ⛔ CMX-177. Before this, a red baseline said only "the suite is NOT GREEN before any
    mutation (`…` exited 1: 3 failed, 1105 passed)" — an exit code and a count name no
    cause, and the operator cannot tell "rework the PR" from "fix base_branch" from "fix the
    judge's box" apart. Observed live 2026-07-25: cmx-174 came back `cannot_verify` three
    times because its baseline carried failures the base branch (``dev``) had ALREADY fixed
    by the time it was judged — the branch was stale, not broken — and a human had to
    manually re-run the suite in a scratch worktree to learn that. CMX-176 closed the
    staleness (the worktree is refreshed from ``origin/<base>`` before the baseline ever
    runs); this closes the diagnosis: if the baseline is STILL red after that refresh, check
    ``origin/<base>`` ALONE, so the report can say which of the two it actually is.

    This never changes whether the run is ``cannot_verify`` — a red baseline blocks nothing
    either way, whatever caused it. It only names the cause in the sentence a human reads.
    """
    if not base_branch:
        return ("the workflow names no `workspace.base_branch`, so the judge could not check "
                "whether this predates the PR")
    ref = f"origin/{base_branch}"
    resolved = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True, text=True, errors="replace",
    )
    if resolved.returncode != 0 or not resolved.stdout.strip():
        return (f"`{ref}` does not resolve in the judge worktree, so the judge could not check "
                "whether this predates the PR — this may be a fresh regression, or the judge's "
                "environment could not see the base branch")
    base_sha = resolved.stdout.strip()

    head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True, text=True, errors="replace",
    )
    if head.returncode != 0 or not head.stdout.strip():
        return "the judge could not read the worktree's own HEAD to compare it against base_branch"
    orig_sha = head.stdout.strip()

    if base_sha == orig_sha:
        # Nothing to separate the PR's own commits from base — the worktree tip IS base.
        return (f"this worktree's HEAD already equals `{ref}` — there is no PR content left to "
                "separate from a base-branch failure; whatever is red here is red on "
                "base_branch itself")

    checkout = subprocess.run(
        ["git", "-C", str(worktree), "checkout", "--quiet", "--detach", base_sha],
        capture_output=True, text=True, errors="replace",
    )
    if checkout.returncode != 0:
        return (f"`{ref}` could not be checked out in the judge worktree "
                f"({(checkout.stderr or '').strip()[:200]}) — the judge could not tell whether "
                "this predates the PR")
    try:
        base_result = run_suite(test_cmd, worktree, timeout)
    finally:
        restore = subprocess.run(
            ["git", "-C", str(worktree), "checkout", "--quiet", "--detach", orig_sha],
            capture_output=True, text=True, errors="replace",
        )
        if restore.returncode != 0:
            # ⛔ CMX-218: name what git actually said, not just that the checkout failed — the
            # next thing a caller does is treat this worktree as the PR's own HEAD, and if
            # that is wrong, "could not restore" with no detail sends a human to re-derive
            # from scratch what git already reported once, on this line, and threw away.
            log.error("judge: could not restore worktree %s to %s after the base_branch "
                      "diagnostic (git exited %d: %s)", worktree, orig_sha, restore.returncode,
                      (restore.stderr or "").strip()[:200])

    if not base_result.ok:
        return (f"the judge tried `{test_cmd}` against `{ref}` alone and it would not even run "
                f"({base_result.detail}) — treat this as a problem with the judge's own "
                "environment, not a verdict on this PR")
    # ⛔ `ok` only means the subprocess RETURNED — a shell that exits nonzero before a single
    # test is collected (a missing `.venv`, an unresolved dependency, an import blow-up) looks
    # identical to a real failure: `ok=True`, exit code nonzero. The tell is the counts —
    # 0 passed, 0 failed means nothing EVER RAN. Observed live 2026-08-02 on cmx-217:
    # the judge worktree had no `.venv`, `uv run pytest` exited 2 with "No such file or
    # directory", and this function reported "RED ON BASE TOO" — sending the operator to fix
    # `dev`, which was green the whole time. A `uv sync` in the judge's worktree was the actual
    # fix; nothing about base_branch needed touching.
    if base_result.exit_code != 0 and base_result.ran == 0:
        why = base_result.detail or _last_meaningful_line(base_result.tail)
        if base_result.errors == 0:
            return (f"the judge tried `{test_cmd}` against `{ref}` alone and it exited "
                    f"{base_result.exit_code} without running OR erroring a single test (0 "
                    f"passed, 0 failed, 0 errors{': ' + why if why else ''}) — that is the "
                    "judge's OWN worktree failing to even START the suite on this checkout, not "
                    "a real base_branch failure. Treat this as a problem with the judge's "
                    "environment (e.g. a missing `.venv`/dependency), not a verdict on "
                    "base_branch or this PR")
        # ⛔ CMX-218 rework round: `ran == 0` with `errors > 0` is NOT the same clean signal
        # as the all-zeros case above. It could still be the judge's own environment, one
        # layer further into collection — reviewer hit exactly this LIVE: a clone missing
        # `--extra dashboard` produced 40 passed, 45 errors on a full suite; move the
        # broken import into a shared conftest/fixture and it collapses to 0 passed, 0
        # failed, N errors, indistinguishable by count from a GENUINE syntax error already
        # committed on base_branch — which really would be base_branch's problem. Nothing
        # here can tell those two apart from the counts alone, so unlike the all-zeros
        # case, this does NOT claim either "the judge's box" or "RED ON BASE TOO" — an
        # unresolved guess dressed as a fact is worse than an honest unknown.
        return (f"the judge tried `{test_cmd}` against `{ref}` alone and it exited "
                f"{base_result.exit_code} without a single test passing or failing, but "
                f"{base_result.errors} error(s) came out of collection ({_suite_line(base_result)}"
                f"{': ' + why if why else ''}) — this could be the judge's OWN worktree failing "
                "to start the suite one layer further into collection (a missing extra or "
                "dependency breaking a shared import), or a genuine collection-time break "
                "already on base_branch itself, and the counts alone cannot tell those apart. "
                "Treat this as UNRESOLVED — not a verdict on base_branch, this PR, or the "
                "judge's environment")
    if not base_result.green:
        return (f"⛔ RED ON BASE TOO — `{ref}` alone ({_suite_line(base_result)}) is ALSO red. "
                "This failure predates the PR: it needs a fix on base_branch, not rework here")
    return (f"RED ONLY ON THIS BRANCH — `{ref}` alone is green ({_suite_line(base_result)}). "
            "This branch's own commits are what turned the suite red")


_PROSE_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}
_PROSE_BASENAMES = {"LICENSE", "NOTICE", "CHANGELOG", "AUTHORS", "CODEOWNERS"}


def _is_prose_path(name: str) -> bool:
    p = Path(name)
    return p.suffix.lower() in _PROSE_SUFFIXES or p.name in _PROSE_BASENAMES


def _docs_only_diff(worktree: Path, base_branch: str) -> bool | None:
    """Whether EVERY file this PR touches (vs ``base_branch``) is prose, not code.

    ⚖️📄 CMX-205. A docs-only PR has no guard for a mutation to corrupt — ``cannot_verify``
    on it is STRUCTURAL (there was nothing to check), not a finding (something went wrong).
    Before this, both cases wrote the same "the judge proposed NO experiments" sentence, so a
    human reading it could not tell "this PR is prose, act on it" from "this PR has code and
    the judge inexplicably wrote nothing" apart — the routine, expected case and the one
    worth investigating looked identical, which is exactly how a bypass stops being read.

    Returns ``None`` — an unknown, never read as yes or no — when it cannot tell: no
    ``base_branch``, an unresolvable ref, a git failure, or an empty diff.
    """
    if not base_branch:
        return None
    ref = f"origin/{base_branch}"
    resolved = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True, text=True, errors="replace",
    )
    if resolved.returncode != 0 or not resolved.stdout.strip():
        return None
    base_sha = resolved.stdout.strip()
    diff = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--name-only", f"{base_sha}...HEAD"],
        capture_output=True, text=True, errors="replace",
    )
    if diff.returncode != 0:
        return None
    files = [f for f in diff.stdout.splitlines() if f.strip()]
    if not files:
        return None
    return all(_is_prose_path(f) for f in files)


def run_experiments(
    worktree: Path,
    test_cmd: str,
    raw: dict,
    *,
    timeout: float = SUITE_TIMEOUT_SECONDS,
    base_branch: str = "",
) -> Report:
    """Execute every proposed experiment IN THIS WORKTREE and adjudicate each one.

    The worktree is a throwaway detached checkout of the PR head — never the branch's own
    worktree, which a rework agent will later commit from. A mutation that escaped into
    THAT one would be pushed to the PR by the very loop this feeds.

    Three things make the whole report CANNOT VERIFY before a single mutation is applied,
    and each of them is an unknown, not a failure:

    * **the worktree is dirty** — a mutation stacked on unknown edits is not a controlled
      experiment;
    * **the baseline is not green** — ⛔ THE LOAD-BEARING ONE. Every SURVIVED verdict means
      "the suite passed under the mutation", so a suite that was ALREADY passing-for-free
      (or not running at all) makes every mutation survive and every PR block. The judge is
      only ever as trustworthy as the suite it starts from, and it says so when that suite
      is not trustworthy. ⛔ CMX-177: it also names WHICH test failed and, given
      ``base_branch``, whether ``origin/<base_branch>`` alone is ALSO red (this failure
      predates the PR) or green (this branch's own doing) — see
      :func:`_diagnose_red_baseline`;
    * **no experiments at all** — nothing was checked. That is not a clean bill of health.
      ⚖️📄 CMX-205: given ``base_branch``, the report says WHETHER this is because the PR is
      DOCS-ONLY (structurally nothing to mutate — see :func:`_docs_only_diff`) or because the
      judge saw code and proposed nothing anyway (worth investigating) — the two used to read
      as the same unknown.

    ``base_branch`` is optional and used to diagnose a red baseline and a docs-only diff
    (never to change whether the run is ``cannot_verify``, and never touched when neither
    diagnosis applies) — pass "" (the default) when it is not known, and the report says so
    instead of guessing.
    """
    report = Report()
    items = raw.get("experiments") if isinstance(raw, dict) else None
    notes = raw.get("notes") if isinstance(raw, dict) else None
    report.notes = [n for n in notes if isinstance(n, dict)] if isinstance(notes, list) else []

    if _git_dirty(worktree):
        report.cannot_verify = (
            f"the judge worktree {worktree} is not clean — a mutation applied on top of "
            "unknown edits is not a controlled experiment"
        )
        return report

    if not isinstance(items, list) or not items:
        if _docs_only_diff(worktree, base_branch):
            report.cannot_verify = (
                "the judge proposed NO experiments, AND this PR is DOCS-ONLY (every file it "
                f"changes vs `origin/{base_branch}` is prose, not code) — there is "
                "structurally no guard here for a mutation to corrupt. ⛔ This `cannot_verify` "
                "is not a finding about the PR, the judge, or the suite: it still blocks "
                "AUTONOMOUS merge (unknown ≠ safe), but it needs a human's read of the prose "
                "itself, not a rework round."
            )
        else:
            report.cannot_verify = (
                "the judge proposed NO experiments — nothing was corrupted, so nothing was "
                "proven. ⛔ Unknown is never a pass: this is not a clean bill of health, it is "
                "an unreviewed PR."
            )
        return report

    if len(items) > MAX_EXPERIMENTS:
        report.dropped = len(items) - MAX_EXPERIMENTS
        items = items[:MAX_EXPERIMENTS]

    # ⛔ CMX-80: PROVISION BEFORE MEASURING. A missing dependency and a broken guard both
    # come out of the suite as "exit 1", and the judge used to report the first as the
    # second — "the suite is NOT GREEN", on a PR whose code was fine. An environment the
    # judge could not build is an unknown ABOUT THE JUDGE, and it has to say so in those
    # words, naming the cwd, or the next reader debugs the PR instead of the box.
    env_problem = provision_suite_env(worktree)
    if env_problem:
        report.cannot_verify = (
            f"the judge worktree could not be PROVISIONED to run the suite: {env_problem}. "
            "⛔ This is an unknown about the JUDGE'S ENVIRONMENT, not a verdict on this PR: "
            "nothing here says the code is wrong. `hooks.before_run` in the WORKFLOW.md AT "
            "THE REPO ROOT is what builds this worktree (never the copy on the branch under "
            "judgment), and it did not install what `judge.test_cmd` needs."
        )
        return report

    # THE BASELINE — the suite as the PR actually ships it, before anything is touched.
    baseline = run_suite(test_cmd, worktree, timeout)
    report.baseline = baseline
    if not baseline.green:
        # ⛔ CMX-80: name the CAUSE, not just the exit code. `judge_detail` (this string) is
        # what the dashboard shows, and for three weeks it showed "exited 1" on every PR
        # while the suite itself was saying "jsdom is not installed" one pipe away.
        why = baseline.detail or _last_meaningful_line(baseline.tail)
        # ⛔ CMX-177: a count is not a cause either. Name WHICH test(s), and then check
        # `origin/<base_branch>` alone to say whether this is this branch's own doing or
        # already broken upstream — three different actions (rework / fix base / fix the
        # judge's box) that used to collapse into the same "exited 1" dead end.
        named = _failing_test_names(baseline.tail)
        which = f" — failing: {', '.join(named)}" if named else ""
        cause = _diagnose_red_baseline(worktree, test_cmd, base_branch, timeout)
        report.cannot_verify = (
            f"the suite is NOT GREEN before any mutation (`{test_cmd}` exited "
            f"{baseline.exit_code}{': ' + why if why else ''}{which}). {cause}. Every "
            "mutation experiment measures 'did the suite go red?', so a suite that is already "
            "red measures nothing. ⛔ Nothing was blocked and nothing was cleared."
        )
        return report

    for raw_exp in items:
        exp, why = Experiment.parse(raw_exp)
        if exp is None:
            report.outcomes.append(Outcome(
                Experiment(guard=str(raw_exp)[:120], file="?", before="", after=""),
                INVALID, f"the experiment is malformed ({why})", baseline, None, "",
            ))
            continue
        path = (worktree / exp.file).resolve()
        try:
            path.relative_to(worktree.resolve())
        except ValueError:
            # ⛔ `../../etc/hosts`. The judge writes to the filesystem; it writes INSIDE the
            # throwaway worktree or it does not write.
            report.outcomes.append(Outcome(
                exp, INVALID, f"{exp.file} is outside the judge worktree", baseline, None, "",
            ))
            continue

        applied, reason, original = apply_mutation(path, exp.before, exp.after)
        try:
            parses, parse_detail = parse_check(path) if applied else (True, "")
            mutated = run_suite(test_cmd, worktree, timeout) if applied else None
            report.outcomes.append(
                adjudicate(exp, applied, reason, parses, parse_detail, baseline, mutated)
            )
        finally:
            # ⛔ ALWAYS. The next experiment's baseline is this file, unmutated.
            restored = True
            restore_detail = ""
            if applied and original is not None:
                try:
                    path.write_text(original)
                except OSError as e:
                    # ⛔ CMX-218: the write itself raised — say what it raised. This is what
                    # was actually observed, not a guess at why (permissions, a vanished
                    # parent dir, disk full all raise OSError and all read differently here).
                    restored = False
                    restore_detail = f"writing the original content back raised: {e}"
                else:
                    readback = path.read_text()
                    restored = readback == original
                    if not restored:
                        # ⛔ The write did not raise, but what is on disk now is neither the
                        # mutation nor the original — something else touched this file between
                        # the write and the read-back. Report the observed sizes, not a cause;
                        # a cause here would be invented.
                        restore_detail = (
                            f"the write did not raise, but reading {path} back afterward got "
                            f"{len(readback)} chars where the original was {len(original)} — "
                            "something else may have written to this file concurrently"
                        )
        if not restored:
            # ⛔ THE ARTIFACT IS NOW CONTAMINATED. Every experiment after this one would run
            # against a file still carrying the last mutation, so its "the suite went green"
            # would be about a codebase nobody wrote. Stop, and take the WHOLE report down
            # with it — including the findings already in hand, which were measured before
            # the contamination but are not worth the risk of being wrong about. This is the
            # one path where `cannot_verify` and `outcomes` are both non-empty, and it is
            # exactly why `Report.blocking` refuses to block on a cannot-verify report
            # whatever its outcomes look like.
            report.cannot_verify = (
                f"{exp.file} could NOT be restored after its mutation — the judge worktree is "
                "contaminated and every measurement after this point would be about code "
                f"nobody wrote{': ' + restore_detail if restore_detail else ''}. ⛔ Nothing was "
                "blocked and nothing was cleared."
            )
            log.error("judge: could not restore %s — abandoning the whole report (%s)", path,
                      restore_detail or "no further detail")
            break

    return report


# --- the verdict: what gets written, and where -------------------------------


def _suite_line(s: SuiteResult | None) -> str:
    if s is None:
        return "(the suite never ran)"
    if not s.ok:
        return s.detail or "(the suite could not be run)"
    return (f"exit {s.exit_code} — {s.passed} passed, {s.failed} failed, {s.errors} error(s)")


def _why_the_suite_was_not_green(s: SuiteResult | None) -> str:
    """The suite's own last words, verbatim, when it was not green.

    ⛔ CMX-80. A judge that CANNOT VERIFY says so loudly — but for three weeks it said only
    "the suite is NOT GREEN before any mutation (`…` exited 1)", and an exit code names no
    cause. The cause was jsdom, absent from every judge worktree, and the suite had been
    printing that in plain English into a pipe nobody read. An unknown has to carry enough
    to be FIXED, or it is just a shrug with a timestamp.
    """
    if s is None or s.green or not (s.tail or "").strip():
        return ""
    return "\n".join([
        "",
        "<details><summary>what the suite actually said (its last output)</summary>",
        "",
        "```",
        s.tail.strip()[-SUITE_TAIL_CHARS:],
        "```",
        "",
        "</details>",
    ])


def _notes_section(notes: list[dict]) -> str:
    """Judgment findings. ⛔ They are POSTED. They never send anything back."""
    if not notes:
        return ""
    lines = [
        "\n---\n\n### 💬 Non-blocking notes (judgment, not fact)\n",
        "These are opinions, so they block nothing and cost no rework round. Take them or "
        "leave them.\n",
    ]
    for note in notes:
        title = str(note.get("title") or "note").strip()
        body = str(note.get("body") or "").strip()
        lines.append(f"- **{title}**" + (f" — {body}" if body else ""))
    return "\n".join(lines) + "\n"


def block_body(report: Report, pr_url: str | None, test_cmd: str) -> str:
    """The verdict a SURVIVED mutation writes — stated as the fact it is."""
    parts = [
        "## ⚖️ THE JUDGE — a guard on this PR SURVIVED DELIBERATE CORRUPTION",
        "",
        "This is not a review of your code, and it is not an opinion. Each finding below is "
        "a **mutation chela applied itself**, in a throwaway checkout of this PR's head: the "
        "file really changed (read back from disk), it still parsed, and "
        f"`{test_cmd}` — **green on this branch before the mutation** "
        f"({_suite_line(report.baseline)}) — **still passed with the corruption in place**.",
        "",
        "⛔ **A guard that survives corruption is not a guard.** The feature may work "
        "perfectly; the thing meant to PROVE it works cannot fail, so nothing is protecting "
        "it from the next change.",
        "",
    ]
    for i, o in enumerate(report.blocking, 1):
        label = "WIRING" if o.experiment.kind == "wiring" else "MUTATION"
        parts += [
            f"### {i}. [{label}] {o.experiment.guard}",
            "",
            f"**File:** `{o.experiment.file}`",
            "",
            "```diff",
            *[f"- {line}" for line in o.experiment.before.splitlines()],
            *[f"+ {line}" for line in o.experiment.after.splitlines()],
            "```",
            "",
            "* the mutation applied: **yes** (the file on disk changed)",
            f"* the file still parses: **yes** ({o.parse_detail})",
            f"* the suite under the mutation: **{_suite_line(o.mutated)}** → **STILL GREEN**",
            "",
        ]
    parts += [
        "### What to do",
        "",
        "1. Make the guard actually guard: it must FAIL when the invariant is violated. "
        "Re-apply the diff above by hand and watch your test go red — if it does not, the "
        "test is asserting something other than what the PR claims.",
        "2. ⛔ Do NOT delete the mutation from the code (it is not in your branch — it was "
        "applied to a throwaway copy and reverted). Fix the **test**.",
        "3. Re-run the suite, push to this branch, and `chela task-finished <task-id>`.",
        "",
        f"_The judge never merges and never approves. PR: {pr_url or '(none on the run row)'}._",
    ]
    return "\n".join(parts) + _notes_section(report.notes)


def comment_body(report: Report, pr_url: str | None, test_cmd: str) -> str:
    """The PR comment for a run the judge did NOT send back — clean, or cannot-verify.

    ⛔ A cannot-verify says so LOUDLY and is not dressed up as a pass. That is the doctor
    rule and the CI gate's ``unknown`` rule, and it is the same rule: a thing nobody could
    evaluate is never green.
    """
    if report.cannot_verify:
        head = [
            "## ⚖️ THE JUDGE — ⚠️ CANNOT VERIFY (this is NOT an approval)",
            "",
            f"**{report.cannot_verify}**",
            "",
            "Nothing was sent back and nothing was cleared: an unknown is never a pass, and "
            "never a fail either. This PR is a human's to review.",
        ]
        tail = _why_the_suite_was_not_green(report.baseline)
        if tail:
            head.append(tail)
    else:
        head = [
            "## ⚖️ THE JUDGE — every guard held",
            "",
            f"chela corrupted each guard this PR adds and re-ran `{test_cmd}` "
            f"(baseline: {_suite_line(report.baseline)}). **Every mutation made the suite go "
            "red** — the guards guard.",
            "",
            "⛔ This is not an approval and it is not a merge: the judge only ever reports "
            "whether the PR's own proof can fail. The merge is still the orchestrator's call.",
        ]
    if report.outcomes:
        head += ["", "| experiment | file | verdict | why |", "|---|---|---|---|"]
        for o in report.outcomes:
            why = o.reason.replace("|", "\\|").replace("\n", " ")
            head.append(
                f"| {o.experiment.guard[:60]} | `{o.experiment.file}` | **{o.verdict}** | {why} |"
            )
    if report.dropped:
        head += ["", f"⚠️ {report.dropped} further experiment(s) were proposed and **not run** "
                     f"(the cap is {MAX_EXPERIMENTS} — each one re-runs the whole suite)."]
    head += ["", f"_PR: {pr_url or '(none on the run row)'} — posted by the dispatcher's judge._"]
    return "\n".join(head) + _notes_section(report.notes)


def load_experiments(path: str | Path) -> tuple[dict, str]:
    """Read the experiments file the judge agent wrote. Returns ``(data, error)``."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text())
    except FileNotFoundError:
        return {}, f"{p} does not exist — the judge wrote no experiments"
    except (OSError, json.JSONDecodeError, ValueError) as e:
        return {}, f"{p} is not readable JSON: {e}"
    if not isinstance(raw, dict):
        return {}, f"{p} must be a JSON object with an `experiments` list"
    return raw, ""


def _reprovision_worktree(wf, worktree: Path, sha: str, base_branch: str) -> str:
    """Rebuild a REAPED judge worktree at ``sha`` — the run's CURRENT head, never the sha a
    stale verdict was recorded against. Returns ``""`` on success, else why it could not.

    ⚖️🕳️ CMX-201: ``_cleanup`` reaps the throwaway worktree the moment a verdict publishes
    (CMX-164), so a PR that fixes exactly the guard a `blocked` verdict named has no way back
    to `clean` short of a whole new dispatch round — the only thing that could re-check it
    was gone. This is the same throwaway detached checkout ``_spawn_judge`` makes
    (:func:`chela.worktree.detached_worktree`, idempotent, never the run's own branch
    worktree), followed by the same base-branch catch-up ``_spawn_judge`` runs before a judge
    ever sees the tree (CMX-176) — so a re-run measures the PR exactly as a fresh judge would.
    """
    from chela import dispatcher
    from chela.worktree import BranchGone

    if not sha:
        return f"the judge worktree {worktree} is gone and this run has no pr_head_sha to " \
               "re-check it out from"
    try:
        dispatcher.detached_worktree(wf.path.parent, sha, worktree)
    except (BranchGone, subprocess.CalledProcessError, OSError) as e:
        detail = getattr(e, "stderr", None) or str(e)
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        return (f"the judge worktree {worktree} is gone and could not be rebuilt at "
                f"{sha[:12]}: {str(detail).strip()[:300]}")
    return dispatcher._refresh_judge_worktree(wf.path.parent, worktree, base_branch)


def judge_run(ident: str, experiments_path: str | Path, *, cleanup: bool = True) -> dict:
    """Execute the judge's experiments and PUBLISH the verdict. The judge agent's last step.

    ⛔ It drives the EXISTING carrier — ``dispatcher.request_changes`` — and adds no second
    path back into the loop. Everything CMX-68 already guarantees therefore still holds: the
    compare-and-swap (a PR a human merged while the judge was running moves to ``done`` and
    this writes NOTHING — no resurrection), the verdict history, the PR comment, the re-spawn
    into the ORIGINAL worktree, ``CHELA_MAX_REWORKS``, the escalation to ``needs_human``.

    ⛔ A judge BLOCK spends a rework round exactly as a human's ``--request-changes`` does —
    it is the same budget, and that is what stops the judge from judging its own rework
    forever.

    ⛔ It never merges and never approves. A clean run is left in ``awaiting_review``, where
    the orchestrator finds it.
    """
    from chela import dispatcher, workflow

    run = dispatcher.resolve_run(ident)
    if run is None:
        return {"ok": False, "error": f"no run matches {ident!r}"}
    task_id = run["task_id"]
    wf_path = run.get("workflow_path")
    # ⚖️🕳️ CMX-221: the token that proves THIS call still owns the judge slot when it
    # reaches `_cleanup` — see that function for why a stale call must never act on it.
    judge_epoch = run.get("judge_window_epoch")
    try:
        wf = workflow.load_workflow(wf_path) if wf_path else None
    except Exception as e:            # a WORKFLOW.md that stopped parsing mid-judgment
        wf = None
        log.warning("judge: %s: %s", wf_path, e)
    if wf is None:
        dispatcher.set_judge_state(task_id, J_CANNOT_VERIFY, "the workflow could not be read")
        return {"ok": False, "task_id": task_id,
                "error": f"the workflow {wf_path!r} could not be read"}

    test_cmd = judge_test_cmd(wf)
    worktree = judge_worktree_path(wf, task_id)
    repo_dir = str(wf.path.parent)
    pr_url = run.get("pr_url")

    # ⚖️🕳️ CMX-221 round 2: OBJECTIVE 1 was exclusive execution, not just guarded cleanup —
    # a dispatcher-launched judge and a manual `chela judge run` for the same task (the
    # documented way an operator clears a stale verdict) land on the identical worktree and
    # would mutate/restore each other's files concurrently. Claim the slot BEFORE touching
    # anything; a live claim held by someone else REFUSES loudly instead of racing them.
    claim_error = _claim_judge_slot(worktree, task_id)
    if claim_error:
        log.warning("judge: %s: refusing to start — %s", task_id, claim_error)
        return {"ok": False, "task_id": task_id, "error": claim_error}

    # ⛔ CMX-164: the judge worktree already exists on disk by this point (`_spawn_judge`
    # created it before this ever ran), and MUST be reaped whether this call finishes or
    # blows up — a `run_experiments`/`request_changes`/`set_judge_state` exception must not
    # leak the directory forever. `finally`, not a happy-path call at the bottom.
    try:
        raw, err = load_experiments(experiments_path)
        base_branch = wf.get("workspace", "base_branch", default="master")
        base_branch = base_branch if isinstance(base_branch, str) else ""
        reprovisioned = False
        if err:
            report = Report(cannot_verify=err)
        elif not test_cmd:
            report = Report(cannot_verify="this workflow sets no `judge.test_cmd` — there is no "
                                          "suite to run a mutation against")
        elif not worktree.is_dir():
            stale = _reprovision_worktree(wf, worktree, run.get("pr_head_sha") or "", base_branch)
            if stale:
                report = Report(cannot_verify=stale)
            else:
                reprovisioned = True
                report = run_experiments(
                    worktree, test_cmd, raw, timeout=judge_suite_timeout(wf),
                    base_branch=base_branch,
                )
        else:
            report = run_experiments(
                worktree, test_cmd, raw, timeout=judge_suite_timeout(wf),
                base_branch=base_branch,
            )
        # A worktree this call rebuilt was checked out at the run's CURRENT head — stamp
        # `judge_sha` to match so the DB record of what was judged is never stale, and the
        # automatic per-sha trigger does not immediately re-spawn a redundant judge on the
        # very commit this call just verified.
        judged_sha = run.get("pr_head_sha") if reprovisioned else None

        blocking = report.blocking
        result = {"ok": True, "task_id": task_id, "state": report.state,
                  "blocking": len(blocking), "outcomes": [o.as_dict() for o in report.outcomes],
                  "cannot_verify": report.cannot_verify, "notes": len(report.notes)}

        if blocking:
            body = block_body(report, pr_url, test_cmd or "?")
            # ⚖️🕳️ CMX-228: POST FIRST, unconditionally — never behind the CAS below.
            # Before this, the ONLY way this comment reached the PR was inside
            # `request_changes`, past its `status == 'awaiting_review'` check AND its
            # compare-and-swap — both of which return early, with NO comment posted, the
            # moment a human merges the PR (or CI gets there first) while the judge is
            # still mid-run. That is the one race a mutation-testing finding cannot afford
            # to lose to: it is the ONLY record that a guard survived corruption — unlike a
            # CI failure, GitHub shows nothing else for it — so the run most likely to have
            # moved out from under a slow judge is exactly the one whose verdict most needed
            # to survive the race. Inverted severity: the `else` branch below posts a clean
            # verdict with no gate at all, while the more important blocking one silently
            # dropped. Posting here, before either check, makes "always shown" literal; the
            # CAS below still guards the RUN ROW (no resurrecting an already-merged run) —
            # it must never gate whether the finding is SHOWN.
            posted, post_detail = dispatcher._post_pr_comment(pr_url, repo_dir, body)
            if not posted:
                log.warning("judge: %s blocking verdict did NOT post to the PR: %s",
                            task_id, post_detail)
            verdict = dispatcher.request_changes(task_id, body, post_comment=False)
            if not verdict.get("ok"):
                # ⚖️🧊 CMX-239: The CAS refused it: the row moved under us (a human merged it,
                # or the CI gate got there first). The RUN ROW was not written — but the
                # comment above was posted regardless, so the finding itself was not lost.
                #
                # ⛔ This is NOT a `cannot_verify` — the judge DID verify, and a guard
                # SURVIVED corruption. Recording `J_CANNOT_VERIFY` here (as this used to)
                # downgrades a confirmed BLOCKING finding to the same shrug-tier "the judge
                # couldn't do its job" state as a launch failure or a flaky worktree. That is
                # the inverted-severity bug CMX-228 already fixed for the PR comment — this is
                # its twin, one layer down: the DB column (and everything that reads it — the
                # inbox event, `chela status`, the retry trigger) still told the weaker story.
                # A human skimming "cannot verify, needs a look" reads as an unknown; a run
                # that already MERGED with a guard proven to survive corruption is not an
                # unknown, it is the most urgent verdict this judge can produce.
                #
                # And it is NOT plain `J_BLOCKED` either — that value also sits on a row long
                # after it settles (through rework rounds, even through an eventual
                # `needs_human` escalation once the run genuinely reached `changes_requested`),
                # so reusing it here would make an unrelated LATER status change on an
                # ordinary blocked run misread as this race. `J_BLOCKED_RACE` means exactly
                # one thing — the finding is real and the row never moved to reflect it — and
                # is what lets `inbox.run_events` raise it at full severity regardless of what
                # the row became, unambiguously, and what stops the per-sha retry trigger from
                # wasting another mutation pass re-discovering a verdict that is already
                # definitive.
                moved = dispatcher.resolve_run(task_id)
                log.warning("judge: %s found %d blocking finding(s), but the run row was not "
                            "updated (already %r): %s", task_id, len(blocking),
                            moved.get("status") if moved else "gone", verdict.get("error"))
                dispatcher.set_judge_state(
                    task_id, J_BLOCKED_RACE,
                    "a guard SURVIVED corruption, but the run moved on before it could be "
                    f"sent back: {verdict.get('error')}",
                    sha=judged_sha,
                )
                result.update(ok=False, state=J_BLOCKED_RACE, error=verdict.get("error"))
            else:
                dispatcher.set_judge_state(
                    task_id, J_BLOCKED,
                    "; ".join(f"{o.experiment.guard}: SURVIVED" for o in blocking)[:500],
                    sha=judged_sha,
                )
                log.warning("judge: %s SENT BACK — %d guard(s) survived corruption",
                            task_id, len(blocking))
                result["round"] = verdict.get("round")
            result["comment_posted"] = posted
        else:
            body = comment_body(report, pr_url, test_cmd or "?")
            posted, post_detail = dispatcher._post_pr_comment(pr_url, repo_dir, body)
            if not posted:
                log.warning("judge: %s clean verdict did NOT post to the PR: %s",
                            task_id, post_detail)
            dispatcher.set_judge_state(
                task_id, report.state, report.cannot_verify or "every guard held",
                sha=judged_sha,
            )
            log.info("judge: %s → %s (%d experiment(s))", task_id, report.state,
                     len(report.outcomes))
            result["comment_posted"] = posted

        return result
    finally:
        _release_judge_slot(worktree)
        if cleanup:
            _cleanup(wf, task_id, run.get("branch_name") or "", judge_epoch)


def _judge_lock_path(worktree: Path) -> Path:
    """A SIBLING of the throwaway worktree, never inside it — `_cleanup`'s `remove_worktree`
    only knows how to delete the worktree itself, and `run_experiments` applies/restores
    files INSIDE it; keeping the lock outside means neither can ever touch it by accident.
    """
    return worktree.parent / f".{worktree.name}.judgelock"


def _read_judge_lock(lock_path: Path) -> dict | None:
    try:
        data = json.loads(lock_path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _judge_lock_owner_alive(lock: dict) -> bool:
    """Is the process that wrote this lock still THAT process — not just any process that
    happens to have the same pid now (CMX-219's lesson: the kernel recycles pids, so a bare
    pid match can make a dead owner look live again). ``started`` is the pid's ``/proc``
    start time at claim time; a live re-read that still matches proves identity.

    When either side is unreadable, identity can't be proven — but unlike CMX-219's tier
    (where an unproven match must NOT be trusted as "same process"), here the fallback still
    needs SOME answer, so it degrades to the weaker "does the pid exist at all" signal rather
    than declaring the claim permanently unrefusable.

    ⛔ THE 1.0s WINDOW IS LOAD-BEARING — do NOT "fix" it to exact equality. CMX-219 rules
    out a tolerance for ITS comparison, and applying that lesson here would look right and
    break this: the two sides can come from DIFFERENT sources. :func:`sessions.proc_started`
    reads ``/proc`` with sub-second precision (…040.97) but falls back to
    :func:`sessions._sh_started`, which parses ``ps -o lstart=`` — an absolute timestamp with
    **whole-second** resolution (…040.00). A lock written while ``/proc`` was readable and
    re-read through the fallback therefore differs by up to one second on a process that
    never moved. CMX-219's comparison is safe at exact equality because both of its sides
    come from the same reader on the same call path; this one is not. The window is the
    fallback's resolution — one second — and nothing wider.
    """
    pid = lock.get("pid")
    if not isinstance(pid, int):
        return False
    from chela import sessions

    started = lock.get("started")
    live_started = sessions.proc_started(pid)
    if started is None or live_started is None:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True            # exists, just not ours to signal (e.g. permission)
        return True
    return abs(live_started - started) < 1.0


def _claim_judge_slot(worktree: Path, task_id: str) -> str | None:
    """Claim the judge slot for ``task_id`` before touching its worktree. ``None`` on
    success; an error string, meant to be returned to the caller verbatim, if someone else
    holds it live right now.

    ⚖️🕳️ CMX-221 round 2: OBJECTIVE 1 asked for EXCLUSIVE execution, not just guarded
    cleanup. A dispatcher-launched judge stamps `judge_window_epoch` at spawn (see
    `_cleanup`), but a manual `chela judge run` never does — it only READS that column — so
    two calls for the same task always carried the identical epoch and that guard was a
    no-op for exactly the collision this closes: an operator's `chela judge run` invoked
    while a dispatcher-launched judge is still in flight on the same task (the documented way
    to clear a stale verdict). The dispatcher's own spawned agent ends by calling this exact
    function too — `judge_run` is "the judge agent's last step" whichever way it started — so
    claiming HERE, independent of tmux and the dispatcher entirely, closes the gap for both
    shapes at once, and does it BEFORE any mutation/restore work starts rather than only at
    the final cleanup.

    A stale claim (the owning process is gone) is taken over silently, not refused forever —
    a crashed judge that never released its slot must not wedge every future judge on this
    task; that would trade one bug for a worse one.
    """
    lock_path = _judge_lock_path(worktree)
    existing = _read_judge_lock(lock_path)
    if existing is not None and _judge_lock_owner_alive(existing):
        return (f"a judge (pid {existing.get('pid')}) is already running for {task_id} in "
                f"this worktree — refusing to share it. If that process is actually gone, "
                f"its claim will be taken over automatically on the next attempt.")
    from chela import sessions

    pid = os.getpid()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({
        "pid": pid, "started": sessions.proc_started(pid), "task_id": task_id,
        "claimed_at": time.time(),
    }))
    return None


def _release_judge_slot(worktree: Path) -> None:
    """Best-effort: drop the claim this call made, so a later run can reclaim it. Only
    removes the lock if it still names THIS process — never a later claim, so a wrong delete
    here can't reopen the exact race this whole mechanism exists to close."""
    lock_path = _judge_lock_path(worktree)
    existing = _read_judge_lock(lock_path)
    if existing is not None and existing.get("pid") == os.getpid():
        try:
            lock_path.unlink()
        except OSError:
            pass


def judge_lock_live(worktree: Path) -> bool:
    """Is a judge for this worktree claimed by a process that is STILL ALIVE, right now?

    ⚖️🕳️ CMX-229 Objective 2. A thin public wrapper around the exact liveness test
    :func:`_claim_judge_slot` already trusts (pid + ``/proc`` start time, CMX-219) — for a
    caller (the dispatcher's judge watchdog) that needs to ask "is someone live in here"
    WITHOUT claiming the slot for itself. Measured live on CMX-227: the watchdog reaped a
    judge worktree/window (SIGKILL, exit 137) mid-``chela judge run`` because its only
    liveness signal was that tick's tmux window snapshot — a signal with no cross-check.
    This gives it a second, independent one to consult before tearing anything down.

    ``False`` on no lock at all (nothing claimed) or a lock whose owner is provably gone —
    both are "safe to reap", the same answer :func:`_claim_judge_slot` gives a stale claim.
    """
    lock = _read_judge_lock(_judge_lock_path(worktree))
    return lock is not None and _judge_lock_owner_alive(lock)


def _cleanup(wf, task_id: str, branch: str, judge_epoch: str | None) -> None:
    """Drop the throwaway worktree, then kill the judge's own tmux window. Best-effort.

    Ordered: the run row is already written, so anything that fails here costs a directory,
    never a verdict. The window is killed LAST because killing it kills this process.

    ⚖️🕳️ CMX-221: guarded by the SAME `judge_window_epoch` CAS that `_launch_agent` stamps
    on every judge spawn (CMX-97's judge-window identity fix). The judge worktree is keyed
    only by `task_id` (see `judge_worktree_path`), so if the watchdog ever declares THIS
    call's judge dead on a stale read (a slow-but-alive judge past `JUDGE_TIMEOUT_SECONDS`,
    or a `live_windows` snapshot that missed it) and respawns a replacement while this call
    is still mid-flight, both calls land on the identical directory and the identical window
    name. Whichever `_cleanup` runs first would delete the other's live workspace out from
    under it. ⛔ This is a real race of the SAME FAMILY as the evening's three misreports
    (2026-08-02) — found by reading the code, NOT the one actually observed that night: the
    watchdog's timeout arm needs `JUDGE_TIMEOUT_SECONDS` (60min) to fire and every run in
    question took ~90s, and a runs-DB query for both watchdog verdict strings ("window
    disappeared", "did not finish in") returns 0 rows. That mechanism is RULED OUT as the
    cause of those three; this guard closes an adjacent, still-real hole regardless. So this
    re-reads the run row RIGHT NOW and only acts if `judge_window_epoch` still matches what
    this call was launched under; a mismatch means a newer judge already took the slot, and
    the stale call does nothing — no worktree removal, no window kill — leaving both to
    whoever actually owns them now.
    """
    from chela import dispatcher as _dispatcher
    from chela.dispatcher import _kill_windows_named
    from chela.worktree import remove_worktree

    current = _dispatcher.resolve_run(task_id)
    still_owns = current is not None and current.get("judge_window_epoch") == judge_epoch
    if not still_owns:
        log.warning(
            "judge: %s: a newer judge now owns this task (judge_window_epoch changed under "
            "us) — skipping cleanup so its worktree and window are left alone", task_id,
        )
        return
    try:
        remove_worktree(wf.path.parent, judge_worktree_path(wf, task_id))
    except Exception:
        log.warning("judge: could not remove the judge worktree for %s", task_id, exc_info=True)
    if branch:
        _kill_windows_named(judge_window_name(branch))


# --- where the judge lives: config, paths, names -----------------------------


def judge_test_cmd(wf) -> str:
    """The suite a mutation is measured against. From WORKFLOW.md, NEVER from the agent."""
    cmd = wf.get("judge", "test_cmd", default=None)
    return cmd.strip() if isinstance(cmd, str) and cmd.strip() else ""


def judge_suite_timeout(wf) -> float:
    try:
        return float(wf.get("judge", "suite_timeout_seconds", default=SUITE_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        return SUITE_TIMEOUT_SECONDS


def judge_enabled(wf) -> bool:
    """Is the judge on for this workflow?

    Off unless a ``judge.test_cmd`` exists — there is nothing to run a mutation against, and
    a judge with no suite could only ever produce opinions, which may not block anything.
    ``judge.enabled: false`` and ``CHELA_JUDGE=0`` are the two kill switches (the config one
    is per workflow; the env one stops the whole fleet judging).
    """
    from chela.config import JUDGE_ENABLED

    if not JUDGE_ENABLED:
        return False
    if wf.get("judge", "enabled", default=True) is False:
        return False
    return bool(judge_test_cmd(wf))


def judge_worktree_path(wf, task_id: str) -> Path:
    """A THROWAWAY, detached checkout — ⛔ never the run's own worktree.

    The judge writes corruptions into files. The run's worktree is what a rework agent
    commits and pushes from, and a mutation left behind in it (a crash, a kill -9) would be
    pushed to the PR by the very loop that spawned the judge. So the judge gets a directory
    of its own, keyed by task id, and it is removed when the verdict is published.
    """
    from chela.workflow import resolve_workspace_root

    return (resolve_workspace_root(wf) / f"judge-{task_id}").resolve()


def judge_window_name(branch: str) -> str:
    return f"judge-{branch}"


def experiments_path(worktree: Path) -> Path:
    """Where the judge agent writes its proposals. Inside the throwaway worktree, which is
    also why it never lands in a commit: the directory is deleted with the verdict."""
    return worktree / ".chela-judge-experiments.json"
