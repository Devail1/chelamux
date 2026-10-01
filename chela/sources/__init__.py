from __future__ import annotations
import re
from dataclasses import dataclass

from chela.workflow import WorkflowDef


@dataclass
class Task:
    id: str               # stable hash of (source-relative-path, line text)
    title: str            # human-readable, e.g. the TODO line text
    file: str             # absolute path of source file ("" for non-file sources)
    line_number: int      # 1-based (issue number for gh_issues)
    raw: str              # original line as written (issue URL for gh_issues)
    # The full multi-line brief when the source can capture one — for the markdown
    # source, `title` + the bullet's indented continuation block (its OBJECTIVE/
    # BOUNDARIES/GUARDS/VERIFY paragraphs), dedented; for gh_issues, the issue body
    # (see chela.sources.gh_issues.GhIssuesSource); `None` for a bare one-line task
    # or an empty/whitespace-only body.
    body: str | None = None
    # Ids of tasks this one must not be CLAIMED before — the tracker's blocking
    # edges (see chela.sources.markdown's `depends:` marker). Empty for a source
    # that has no notion of dependencies (gh_issues) or a task that declares none.
    # A dependency is satisfied only once its task is struck done in the tracker;
    # see chela.dispatcher._ready, the sole place this is enforced.
    depends: tuple[str, ...] = ()
    # ⚖️🎚️ CMX-405. The task's RISK level — one of :data:`RISK_LEVELS`. It scales ONLY how
    # widely the judge searches (its experiment cap) and how many rework rounds the run
    # gets; it never changes what a surviving mutation means (it still BLOCKS). Set from
    # the TRACKER alone — a `<!-- risk: ... -->` marker (markdown) or a `risk:<level>`
    # label (gh_issues) — and, when neither is present, inferred from the brief's
    # BOUNDARIES (see :func:`infer_risk`). `risk_reason` records which of those it was.
    risk: str = "normal"
    risk_reason: str = "default"


# ⚖️🎚️ CMX-405 — ordered LOWEST to HIGHEST stakes; `highest_risk` relies on the order.
RISK_LEVELS = ("low", "normal", "high")
DEFAULT_RISK = "normal"

# The paths/words whose presence in a brief's BOUNDARIES makes an UNMARKED task `high`:
# the dispatcher, the judge, the merge gate/contract, the sandbox, the inbox, and anything
# touching a secret or token. ⛔ Conservative on purpose — a false `high` costs a wider
# search, a false `low` costs a narrower one on exactly the code that guards the rest.
_HIGH_RISK_RE = re.compile(
    r"(?<![\w])(?:dispatcher\.py|judge\.py|contract\.py|mergegate\.py|inbox\.py)"
    r"|sandbox|secret|token",
    re.IGNORECASE,
)
# The BOUNDARIES paragraph of a brief: from its `BOUNDARIES` heading to the next heading
# of the same brief shape (`**GUARDS**`, `VERIFY.`, …) or the end of the text.
_BOUNDARIES_RE = re.compile(
    r"\bBOUNDARIES\b(.*?)(?=\*\*(?:WHY|OBJECTIVE|GUARDS|VERIFY|NOTES?)\b"
    r"|^\s*(?:WHY|OBJECTIVE|GUARDS|VERIFY|NOTES?)\b|\Z)",
    re.DOTALL | re.MULTILINE,
)


def normalize_risk(value: object) -> str | None:
    """``value`` as one of :data:`RISK_LEVELS`, or None when it is not one."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    return v if v in RISK_LEVELS else None


def run_risk(value: object) -> str:
    """The risk a RUN ROW's stored value means — the default for NULL/garbage (a row
    claimed before CMX-405, an adopted PR that never came from the tracker)."""
    return normalize_risk(value) or DEFAULT_RISK


def highest_risk(levels) -> str | None:
    """The highest-stakes of ``levels`` (unknown values ignored), or None if none is valid."""
    valid = [lv for lv in (normalize_risk(v) for v in levels) if lv]
    return max(valid, key=RISK_LEVELS.index) if valid else None


def infer_risk(body: str | None) -> str | None:
    """The FALLBACK risk for a task whose tracker entry carries no explicit level.

    The text that matched :data:`_HIGH_RISK_RE` inside the brief's BOUNDARIES — the task is
    then ``high`` — or None (the caller keeps the default). A brief with no BOUNDARIES
    paragraph infers nothing.
    """
    if not body:
        return None
    b = _BOUNDARIES_RE.search(body)
    if not b:
        return None
    m = _HIGH_RISK_RE.search(b.group(1))
    return m.group(0) if m else None


def apply_risk(task: Task, explicit: str | None, explicit_reason: str) -> Task:
    """Set ``task.risk``/``task.risk_reason``: the tracker's explicit level wins; else the
    BOUNDARIES fallback; else :data:`DEFAULT_RISK`. Returns ``task`` for chaining."""
    if explicit:
        task.risk, task.risk_reason = explicit, explicit_reason
        return task
    hit = infer_risk(task.body)
    if hit:
        task.risk = "high"
        task.risk_reason = f"inferred: BOUNDARIES touch {hit!r}"
    else:
        task.risk, task.risk_reason = DEFAULT_RISK, "default"
    return task


def get_source(wf: WorkflowDef):
    kind = wf.get("tracker", "kind")
    if kind == "markdown":
        from chela.sources.markdown import MarkdownSource
        return MarkdownSource(wf)
    if kind == "gh_issues":
        from chela.sources.gh_issues import GhIssuesSource
        return GhIssuesSource(wf)
    raise ValueError(f"Unknown tracker.kind: {kind!r}")
