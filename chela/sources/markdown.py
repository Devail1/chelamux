from __future__ import annotations
import hashlib
import re
import textwrap
from pathlib import Path

from chela.sources import Task
from chela.workflow import WorkflowDef

OPEN_RE = re.compile(r"^\s*-\s*\[\s\]\s*(.+?)\s*$")
DONE_RE = re.compile(r"^\s*-\s*\[[xX]\]\s*(.+?)\s*$")
BLOCKED_RE = re.compile(r"<!--\s*blocked", re.IGNORECASE)
DEPENDS_RE = re.compile(r"<!--\s*depends:\s*(.+?)\s*-->", re.IGNORECASE)
# The optional human-readable "why" a PARKED bullet carries — `<!-- blocked -->` alone,
# with no colon, is valid too; it just has no reason text to show.
BLOCKED_REASON_RE = re.compile(r"<!--\s*blocked\s*:\s*(.*?)\s*-->", re.IGNORECASE)
# Strips a bullet's own trailing `<!-- ... -->` marker(s) down to its bare, human-visible
# title — the same string a `depends: "..."` reference names (see `_resolve_depends`),
# and what a PARKED bullet's id must hash off too (see `parked_tasks_from_text`): a
# human writes the bare title, never the raw marker-attached line.
_TRAILING_COMMENT_RE = re.compile(r"\s*<!--.*?-->\s*")


class MarkdownSource:
    def __init__(self, wf: WorkflowDef):
        rel = wf.get("tracker", "path", default="TODO.md")
        self.workflow_path = wf.path
        self.path = (wf.path.parent / rel).resolve()

    def list_open_tasks(self) -> list[Task]:
        if not self.path.exists():
            return []
        return self.tasks_from_text(self.path.read_text())

    def tasks_from_text(self, text: str) -> list[Task]:
        """The open tasks in `text`, as if it were this tracker's contents.

        Same parse as :meth:`list_open_tasks`, but sourced from a string — which is what
        lets the dispatcher read the tracker straight out of ``origin/<base_branch>`` at
        claim time (``git show``) without touching, or waiting on, the working tree. Task
        ids are unchanged by the detour: an id is the hash of (tracker FILENAME, title),
        so the same line yields the same id whatever blob it came from — and, for the same
        reason, MOVING a line does not re-key it. Reordering the queue can therefore never
        orphan an in-flight run.
        """
        lines = text.splitlines()
        tasks: list[Task] = []
        for i, raw in enumerate(lines, start=1):
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
                body=_task_body(title, lines, i),
                depends=_resolve_depends(self.path.name, title),
            ))
        return tasks

    def list_parked_tasks(self) -> list[Task]:
        if not self.path.exists():
            return []
        return self.parked_tasks_from_text(self.path.read_text())

    def parked_tasks_from_text(self, text: str) -> list[Task]:
        """Every PARKED (`<!-- blocked: ... -->`) bullet in `text`.

        `tasks_from_text` skips these outright — they are not claimable work — which
        used to mean a parked bullet was invisible everywhere: not in Open, and (since
        it lives in TODO.md, not BACKLOG.md) not in Backlog either. It sat in the
        tracker with nothing on the board to show for it (Liav, 2026-08-12: "should we
        see parked in backlog?"). This surfaces the same bullets `tasks_from_text`
        drops, so the dashboard can render them instead of losing them.

        Id and title are hashed/reported off the BARE title (comment stripped) — the
        same treatment `chela.runtime_truth._parked_ids_from_text` gives them for
        `depends:` identity, since a human names this task via its bare visible title,
        never the raw bullet with its own marker attached.
        """
        lines = text.splitlines()
        tasks: list[Task] = []
        for i, raw in enumerate(lines, start=1):
            m = OPEN_RE.match(raw)
            if not m:
                continue
            title = m.group(1).strip()
            if not BLOCKED_RE.search(title):
                continue
            bare = _TRAILING_COMMENT_RE.sub(" ", title).strip()
            reason_m = BLOCKED_REASON_RE.search(title)
            reason = reason_m.group(1).strip() if reason_m else ""
            tasks.append(Task(
                id=_task_id(self.path, bare),
                title=bare,
                file=str(self.path),
                line_number=i,
                raw=raw,
                body=reason or None,
            ))
        return tasks

    def closed_ids_from_text(self, text: str) -> set[str]:
        """Ids of the `- [x]` lines in `text` — the tasks that text considers DONE.

        The counterpart of :meth:`tasks_from_text`: when the dispatcher claims from
        ``origin``'s copy of the tracker it must also honour what ``origin`` has already
        struck, or a task struck on the remote (and not yet pulled into this checkout)
        would look like fresh work.
        """
        ids: set[str] = set()
        for raw in text.splitlines():
            m = DONE_RE.match(raw)
            if m:
                ids.add(_task_id(self.path, m.group(1).strip()))
        return ids

    def close_tasks(self, task_ids: list[str], *, at: Path | None = None) -> dict[str, str]:
        """Flip the `- [ ]` lines for `task_ids` to `- [x]`. Returns id → outcome.

        The dispatcher is this file's SOLE writer (agents never touch the
        tracker — see dispatcher._strike_merged_tasks). Rewrites the file only
        when something actually changed, so calling it twice is a no-op.

        ``at`` redirects the actual read/write to a different copy of this same
        file — the dispatcher's isolated base-write worktree (see
        ``dispatcher._base_write_worktree``), never the interactive checkout
        this source was constructed against. Task ids are still hashed off
        ``self.path.name`` (the filename, not its directory), so the ids match
        whichever copy the strike runs against. Defaults to ``self.path`` so
        every other caller — including the tests that predate the isolated
        worktree — is unaffected.
        """
        path = at if at is not None else self.path
        if not path.exists():
            return {tid: "missing" for tid in task_ids}
        text = path.read_text()
        new_text, results = strike_lines(text, self.path.name, task_ids)
        if new_text != text:
            path.write_text(new_text)
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


def _continuation_block(lines: list[str], bullet_line_no: int) -> list[str]:
    """The lines belonging to the bullet at 1-based `bullet_line_no`: every
    following line that is BLANK or INDENTED (starts with whitespace), stopping
    at the first line that is neither — the next `- [ ]`/`- [x]` bullet, a `## `
    header, or any other column-0 text.

    `lines` is 0-based, so `lines[bullet_line_no]` is exactly the line AFTER the
    bullet (index `bullet_line_no - 1` is the bullet's own line). This is a pure
    lookahead — it does not touch, or desync, the caller's own enumeration, so
    the NEXT bullet's `line_number` is unaffected by however many continuation
    lines this one consumes.
    """
    block: list[str] = []
    for line in lines[bullet_line_no:]:
        if line.strip() == "" or line[:1].isspace():
            block.append(line)
        else:
            break
    return block


def _task_body(title: str, lines: list[str], bullet_line_no: int) -> str | None:
    """The task's full brief: `title` + a blank line + its continuation block
    (see :func:`_continuation_block`), DEDENTED by the block's common leading
    indent and with trailing blank lines stripped. `None` for a bare one-line
    task — no continuation at all, or one that dedents to nothing (e.g. a lone
    blank line before the next section)."""
    block = _continuation_block(lines, bullet_line_no)
    if not block:
        return None
    dedented = textwrap.dedent("\n".join(block)).strip("\n")
    if not dedented:
        return None
    return f"{title}\n\n{dedented}"


def _parse_depends(raw: str) -> tuple[str, ...]:
    """Split a `depends:` marker's payload into the titles it names.

    `;`-separated (not `,` — a title is prose and commonly contains one), each
    optionally wrapped in matching quotes so a title that itself CONTAINS a `;`
    remains expressible. The split is quote-aware — a `;` inside a matched pair
    of quotes does not end the segment — because a naive `raw.split(";")` done
    first, with quote-stripping only applied to the resulting pieces, can never
    recover a title with an embedded `;`: the split has already cut it in two
    before the quotes are even looked at, and no amount of quoting in the
    tracker file can fix that from the outside. A dependency named that way
    would silently resolve to the wrong (nonexistent) ids and read exactly like
    a typo'd reference — permanently blocking its dependent with no way for a
    human to correct it by writing "better" markdown.

    Blank segments (a trailing `;`, `<!-- depends: -->` written with nothing in
    it) are dropped rather than yielding an empty-string dependency nothing can
    ever satisfy.
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for ch in raw:
        if quote is not None:
            current.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch == ";":
            segments.append("".join(current))
            current = []
        else:
            current.append(ch)
    segments.append("".join(current))

    titles = []
    for part in segments:
        part = part.strip()
        if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
            part = part[1:-1].strip()
        if part:
            titles.append(part)
    return tuple(titles)


def _resolve_depends(filename: str, title: str) -> tuple[str, ...]:
    """The ids of the tasks `title` declares via a `<!-- depends: ... -->` marker —
    a dependency is named by the OTHER bullet's title text, hashed the same way
    :func:`_title_id` hashes any task's identity, so it resolves to that task's
    real id regardless of where in the file it lives or what order the two are
    claimed in.
    """
    m = DEPENDS_RE.search(title)
    if not m:
        return ()
    return tuple(_title_id(filename, t) for t in _parse_depends(m.group(1)))


def _title_id(filename: str, title: str) -> str:
    h = hashlib.sha1(f"{filename}\x00{title.strip()}".encode()).hexdigest()
    return h[:12]


def _task_id(file: Path, title: str) -> str:
    return _title_id(file.name, title)
