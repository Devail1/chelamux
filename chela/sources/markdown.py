from __future__ import annotations
import hashlib
import re
from pathlib import Path

from chela.sources import Task
from chela.workflow import WorkflowDef

OPEN_RE = re.compile(r"^\s*-\s*\[\s\]\s*(.+?)\s*$")
BLOCKED_RE = re.compile(r"<!--\s*blocked", re.IGNORECASE)


class MarkdownSource:
    def __init__(self, wf: WorkflowDef):
        rel = wf.get("tracker", "path", default="TODO.md")
        self.workflow_path = wf.path
        self.path = (wf.path.parent / rel).resolve()

    def list_open_tasks(self) -> list[Task]:
        if not self.path.exists():
            return []
        tasks: list[Task] = []
        for i, raw in enumerate(self.path.read_text().splitlines(), start=1):
            m = OPEN_RE.match(raw)
            if not m:
                continue
            title = m.group(1).strip()
            if BLOCKED_RE.search(title):
                continue
            tid = _task_id(self.path, title)
            tasks.append(Task(
                id=tid,
                title=title,
                file=str(self.path),
                line_number=i,
                raw=raw,
            ))
        return tasks


def _task_id(file: Path, title: str) -> str:
    h = hashlib.sha1(f"{file.name}\x00{title}".encode()).hexdigest()
    return h[:12]
