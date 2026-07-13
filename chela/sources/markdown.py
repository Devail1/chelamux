from __future__ import annotations
import hashlib
import re
from pathlib import Path

from chela.sources import Task
from chela.workflow import WorkflowDef

OPEN_RE = re.compile(r"^\s*-\s*\[\s\]\s*(.+?)\s*$")
DONE_RE = re.compile(r"^\s*-\s*\[[xX]\]\s*(.+?)\s*$")
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

    def close_tasks(self, task_ids: list[str]) -> dict[str, str]:
        """Flip the `- [ ]` lines for `task_ids` to `- [x]`. Returns id → outcome.

        The dispatcher is this file's SOLE writer (agents never touch the
        tracker — see dispatcher._strike_merged_tasks). Rewrites the file only
        when something actually changed, so calling it twice is a no-op.
        """
        if not self.path.exists():
            return {tid: "missing" for tid in task_ids}
        text = self.path.read_text()
        new_text, results = strike_lines(text, self.path.name, task_ids)
        if new_text != text:
            self.path.write_text(new_text)
        return results


def strike_lines(
    text: str, filename: str, task_ids: list[str] | set[str]
) -> tuple[str, dict[str, str]]:
    """Mark the checkbox lines whose task id is in `task_ids` as done.

    Pure — takes and returns the file's text — so the dispatcher's strike is
    testable without a repo. Each requested id gets one of three outcomes:

      "struck"  — its `- [ ]` line was flipped to `- [x]`.
      "already" — its line is already `- [x]`. Nothing to do; this is what makes
                  the strike idempotent (an agent that struck its own line out
                  of habit can't turn the merge into a conflict).
      "missing" — nothing hashes to it: a human edited the title or deleted the
                  line. We do NOT fall back to a fuzzy title match — the id *is*
                  the hash of the title, so an edited title is a different task,
                  and guessing would strike the wrong line.

    Only the checkbox is rewritten; the rest of each line — trailing whitespace,
    line endings, the whole body — survives byte-for-byte.
    """
    wanted = set(task_ids)
    results: dict[str, str] = {tid: "missing" for tid in wanted}
    out: list[str] = []
    changed = False
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        m = OPEN_RE.match(line)
        if m:
            tid = _title_id(filename, m.group(1))
            if tid in wanted:
                # OPEN_RE anchored "[ ]" to the bullet, so the first occurrence
                # is the checkbox — a bounded replace can't touch the title.
                raw = raw.replace("[ ]", "[x]", 1)
                results[tid] = "struck"
                changed = True
        else:
            d = DONE_RE.match(line)
            if d:
                tid = _title_id(filename, d.group(1))
                if tid in wanted:
                    results[tid] = "already"
        out.append(raw)
    return ("".join(out) if changed else text), results


def _title_id(filename: str, title: str) -> str:
    h = hashlib.sha1(f"{filename}\x00{title.strip()}".encode()).hexdigest()
    return h[:12]


def _task_id(file: Path, title: str) -> str:
    return _title_id(file.name, title)
