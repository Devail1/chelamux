"""📜 CMX-317. The ``{{standing_rules}}`` prompt variable — a short, versioned rules file.

The orchestrator used to type the same handful of rules into nearly every dispatched
agent by hand (don't wrap ``task-finished`` in ``timeout``, don't restart a killed hand-in,
…), and a hand-in was lost when one agent didn't get one. The rules now live in ONE file
the workflow names (``agent.standing_rules: docs/AGENT_STANDING_RULES.md``, relative to
the repo the WORKFLOW.md sits in), and every dispatch prompt renders it.

A missing or unreadable file must never fail a dispatch or ship a literal
``{{standing_rules}}``: it renders as ``""`` and logs a warning. A workflow that names no
file renders ``""`` silently — the key is opt-in. The seeded file's 40-line cap is a
test (``tests/test_standing_rules.py``), not a render-time check.
"""
from __future__ import annotations

import logging
from pathlib import Path

from chela.workflow import WorkflowDef

log = logging.getLogger(__name__)


# 🧵 CMX-287 — a Linear-tracked run is steered by comments on its issue, which the brief
# persisted at claim time never sees; this line sends every attempt to read them.
TASK_SHOW_RULE = (
    "- Run `chela task show` at the start of every attempt, first dispatch and every rework "
    "alike (read-only: your Linear issue's current description and comments, oldest first). "
    "Comments newer than your brief are the operator's latest steer; where they conflict "
    "with the brief, follow the comment. If it says it could not fetch, report that rather "
    "than assume nothing changed.")


def standing_rules(wf: WorkflowDef) -> str:
    """The workflow's standing rules, plus :data:`TASK_SHOW_RULE` for a Linear tracker."""
    rules = _rules_file(wf)
    if wf.get("tracker", "kind", default=None) != "linear":
        return rules
    return f"{rules}\n{TASK_SHOW_RULE}" if rules else TASK_SHOW_RULE


def _rules_file(wf: WorkflowDef) -> str:
    """The text of the workflow's ``agent.standing_rules`` file, or ``""``.

    ``""`` (no warning) when the key is unset; ``""`` plus a warning when it is set but
    not a usable path, or the file cannot be read.
    """
    raw = wf.get("agent", "standing_rules", default=None)
    if raw is None:
        return ""
    if not isinstance(raw, str) or not raw.strip():
        log.warning("%s: agent.standing_rules is not a file path (%r) — rendering no "
                    "standing rules", wf.path, raw)
        return ""
    path = Path(wf.path).parent / raw.strip()
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as e:
        log.warning("%s: agent.standing_rules file %s could not be read (%s) — rendering "
                    "no standing rules", wf.path, path, e)
        return ""
