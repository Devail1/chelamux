from __future__ import annotations
from dataclasses import dataclass

from chela.workflow import WorkflowDef


@dataclass
class Task:
    id: str               # stable hash of (source-relative-path, line text)
    title: str            # human-readable, e.g. the TODO line text
    file: str             # absolute path of source file ("" for non-file sources)
    line_number: int      # 1-based (issue number for gh_issues)
    raw: str              # original line as written (issue URL for gh_issues)


def get_source(wf: WorkflowDef):
    kind = wf.get("tracker", "kind")
    if kind == "markdown":
        from chela.sources.markdown import MarkdownSource
        return MarkdownSource(wf)
    if kind == "gh_issues":
        from chela.sources.gh_issues import GhIssuesSource
        return GhIssuesSource(wf)
    raise ValueError(f"Unknown tracker.kind: {kind!r}")
