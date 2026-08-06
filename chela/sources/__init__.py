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
    # The full multi-line brief when the source can capture one — for the markdown
    # source, `title` + the bullet's indented continuation block (its OBJECTIVE/
    # BOUNDARIES/GUARDS/VERIFY paragraphs), dedented; `None` for a bare one-line
    # task or a source that has no notion of a continuation (gh_issues — an issue's
    # body isn't fetched here; see chela.sources.gh_issues.GhIssuesSource).
    body: str | None = None
    # Ids of tasks this one must not be CLAIMED before — the tracker's blocking
    # edges (see chela.sources.markdown's `depends:` marker). Empty for a source
    # that has no notion of dependencies (gh_issues) or a task that declares none.
    # A dependency is satisfied only once its task is struck done in the tracker;
    # see chela.dispatcher._ready, the sole place this is enforced.
    depends: tuple[str, ...] = ()


def get_source(wf: WorkflowDef):
    kind = wf.get("tracker", "kind")
    if kind == "markdown":
        from chela.sources.markdown import MarkdownSource
        return MarkdownSource(wf)
    if kind == "gh_issues":
        from chela.sources.gh_issues import GhIssuesSource
        return GhIssuesSource(wf)
    raise ValueError(f"Unknown tracker.kind: {kind!r}")
