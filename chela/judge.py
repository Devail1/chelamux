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
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
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

    Python never exposed this because ``uv run`` re-syncs the venv on every invocation — the
    hook's ``uv sync`` is a speed-up, not a load-bearing step. Node has no equivalent:
    ``npm ci`` runs once or never. This IS that equivalent, and it belongs here, in the code
    the judge worktree executes, rather than in a hook the judged commit cannot reach.
    """
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
            log.error("judge: could not restore worktree %s to %s after the base_branch "
                      "diagnostic", worktree, orig_sha)

    if not base_result.ok:
        return (f"the judge tried `{test_cmd}` against `{ref}` alone and it would not even run "
                f"({base_result.detail}) — treat this as a problem with the judge's own "
                "environment, not a verdict on this PR")
    if not base_result.green:
        return (f"⛔ RED ON BASE TOO — `{ref}` alone ({_suite_line(base_result)}) is ALSO red. "
                "This failure predates the PR: it needs a fix on base_branch, not rework here")
    return (f"RED ONLY ON THIS BRANCH — `{ref}` alone is green ({_suite_line(base_result)}). "
            "This branch's own commits are what turned the suite red")


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

    ``base_branch`` is optional and used ONLY to diagnose a red baseline (never to change
    whether the run is ``cannot_verify``, and never touched if the baseline is green) — pass
    "" (the default) when it is not known, and the report says so instead of guessing.
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
            if applied and original is not None:
                try:
                    path.write_text(original)
                    restored = path.read_text() == original
                except OSError:
                    restored = False
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
                "nobody wrote. ⛔ Nothing was blocked and nothing was cleared."
            )
            log.error("judge: could not restore %s — abandoning the whole report", path)
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

    # ⛔ CMX-164: the judge worktree already exists on disk by this point (`_spawn_judge`
    # created it before this ever ran), and MUST be reaped whether this call finishes or
    # blows up — a `run_experiments`/`request_changes`/`set_judge_state` exception must not
    # leak the directory forever. `finally`, not a happy-path call at the bottom.
    try:
        raw, err = load_experiments(experiments_path)
        if err:
            report = Report(cannot_verify=err)
        elif not test_cmd:
            report = Report(cannot_verify="this workflow sets no `judge.test_cmd` — there is no "
                                          "suite to run a mutation against")
        elif not worktree.is_dir():
            report = Report(cannot_verify=f"the judge worktree {worktree} is gone")
        else:
            base_branch = wf.get("workspace", "base_branch", default="master")
            report = run_experiments(
                worktree, test_cmd, raw, timeout=judge_suite_timeout(wf),
                base_branch=base_branch if isinstance(base_branch, str) else "",
            )

        blocking = report.blocking
        result = {"ok": True, "task_id": task_id, "state": report.state,
                  "blocking": len(blocking), "outcomes": [o.as_dict() for o in report.outcomes],
                  "cannot_verify": report.cannot_verify, "notes": len(report.notes)}

        if blocking:
            body = block_body(report, pr_url, test_cmd or "?")
            verdict = dispatcher.request_changes(task_id, body)
            if not verdict.get("ok"):
                # The CAS refused it: the row moved under us (a human merged it, or the CI gate
                # got there first). Nothing was written, and nothing should be.
                log.info("judge: %s found %d blocking finding(s), but the verdict was not "
                         "written: %s", task_id, len(blocking), verdict.get("error"))
                dispatcher.set_judge_state(
                    task_id, J_CANNOT_VERIFY,
                    f"the run moved while the judge was running: {verdict.get('error')}",
                )
                result.update(ok=False, state=J_CANNOT_VERIFY, error=verdict.get("error"))
            else:
                dispatcher.set_judge_state(
                    task_id, J_BLOCKED,
                    "; ".join(f"{o.experiment.guard}: SURVIVED" for o in blocking)[:500],
                )
                log.warning("judge: %s SENT BACK — %d guard(s) survived corruption",
                            task_id, len(blocking))
                result["round"] = verdict.get("round")
        else:
            body = comment_body(report, pr_url, test_cmd or "?")
            dispatcher._post_pr_comment(pr_url, repo_dir, body)
            dispatcher.set_judge_state(
                task_id, report.state, report.cannot_verify or "every guard held",
            )
            log.info("judge: %s → %s (%d experiment(s))", task_id, report.state,
                     len(report.outcomes))

        return result
    finally:
        if cleanup:
            _cleanup(wf, task_id, run.get("branch_name") or "")


def _cleanup(wf, task_id: str, branch: str) -> None:
    """Drop the throwaway worktree, then kill the judge's own tmux window. Best-effort.

    Ordered: the run row is already written, so anything that fails here costs a directory,
    never a verdict. The window is killed LAST because killing it kills this process.
    """
    from chela.dispatcher import _kill_windows_named
    from chela.worktree import remove_worktree

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
