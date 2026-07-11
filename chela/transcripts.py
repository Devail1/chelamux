"""Read recap + PR-link records from Claude Code session JSONL transcripts.

Each Claude Code session writes a JSONL transcript at
    ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
where <encoded-cwd> replaces every `/` and `.` in the cwd with `-`.

We want two things per agent:
  - the latest `{"type": "system", "subtype": "away_summary"}.content` (recap)
  - the latest `{"type": "pr-link", ...}` record (last PR opened in-session)

Transcripts grow without bound, so we scan from the tail of the file
backwards block-by-block and stop at the first match (or once we have
scanned `max_scan_bytes`). Public helpers return plain dicts / strings
so callers (the Flask dashboard) don't need to know the file layout.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from chela import discovery

log = logging.getLogger(__name__)

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Cap on how far back we scan a transcript looking for a record. Two MB is
# plenty for the latest recap/PR — they are typically within the last few KB.
DEFAULT_MAX_SCAN_BYTES = 2 * 1024 * 1024
_READ_BLOCK = 64 * 1024


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def encode_cwd(cwd: str) -> str:
    """Encode a cwd into Claude Code's `~/.claude/projects/<dir>` name.

    Claude Code replaces both `/` and `.` with `-`, so
    `/home/alice/.chela/worktrees/myproj/abc` becomes
    `-home-alice--chela-worktrees-myproj-abc`.
    """
    return cwd.replace("/", "-").replace(".", "-")


def transcript_path(cwd: str, session_id: str, base: Path | None = None) -> Path:
    """Build the expected transcript path. Existence is the caller's problem."""
    base = base or CLAUDE_PROJECTS_DIR
    return base / encode_cwd(cwd) / f"{session_id}.jsonl"


# ---------------------------------------------------------------------------
# Reverse-tail JSONL scanning
# ---------------------------------------------------------------------------

def _iter_lines_reverse(path: Path, block_size: int = _READ_BLOCK) -> Iterator[bytes]:
    """Yield non-empty lines from `path` in reverse order, reading backwards.

    We keep a single buffer of unparsed bytes at the *start* of the current
    block because the first piece may be a partial line whose tail lives in
    an earlier block. Once we reach the file head we yield whatever is left.
    """
    with path.open("rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        buffer = b""
        while pos > 0:
            read = min(block_size, pos)
            pos -= read
            f.seek(pos)
            buffer = f.read(read) + buffer
            lines = buffer.split(b"\n")
            buffer = lines[0]
            for line in reversed(lines[1:]):
                if line:
                    yield line
        if buffer:
            yield buffer


def latest_record(
    path: Path,
    predicate: Callable[[dict], bool],
    max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES,
) -> dict | None:
    """Return the latest JSON record in `path` matching `predicate`, or None.

    Stops once `max_scan_bytes` of file content has been inspected. Bad JSON
    lines (truncated writes, mid-rotation) are skipped silently.
    """
    if not path.exists():
        return None
    scanned = 0
    try:
        for raw in _iter_lines_reverse(path):
            scanned += len(raw) + 1
            if scanned > max_scan_bytes:
                return None
            try:
                obj = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if predicate(obj):
                return obj
    except OSError as e:
        log.warning("Failed to read transcript %s: %s", path, e)
    return None


# ---------------------------------------------------------------------------
# Typed extractors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PRLink:
    url: str
    number: int | None
    repository: str | None
    timestamp: str | None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "number": self.number,
            "repository": self.repository,
            "ts": self.timestamp,
        }


def _latest_recap_record(path: Path) -> dict | None:
    """Return the latest valid `away_summary` record (non-empty content), or None."""
    rec = latest_record(
        path,
        lambda o: o.get("type") == "system" and o.get("subtype") == "away_summary",
    )
    if not rec:
        return None
    content = rec.get("content")
    return rec if isinstance(content, str) and content.strip() else None


def latest_recap(path: Path) -> str | None:
    """Return the `content` field of the latest `away_summary` system record."""
    rec = _latest_recap_record(path)
    return rec.get("content") if rec else None


def latest_pr(path: Path) -> PRLink | None:
    """Return the latest `pr-link` record as a PRLink (None if missing/invalid)."""
    rec = latest_record(path, lambda o: o.get("type") == "pr-link")
    if not rec:
        return None
    url = rec.get("prUrl")
    if not isinstance(url, str) or not url:
        return None
    return PRLink(
        url=url,
        number=rec.get("prNumber") if isinstance(rec.get("prNumber"), int) else None,
        repository=rec.get("prRepository") if isinstance(rec.get("prRepository"), str) else None,
        timestamp=rec.get("timestamp") if isinstance(rec.get("timestamp"), str) else None,
    )


# ---------------------------------------------------------------------------
# Dashboard-facing helpers
# ---------------------------------------------------------------------------

def transcript_for_cwd(cwd: str | None, base: Path | None = None) -> Path | None:
    """Resolve a working directory → its active transcript path, tmux-natively.

    Claude Code does not surface its session id over tmux, so rather than rely
    on any external session-id map we derive the transcript directory from the
    cwd (``~/.claude/projects/<encoded-cwd>/``) and pick the most-recently-
    modified ``<session-id>.jsonl`` in it — that is the session actively writing
    from that directory. Returns None if cwd is empty, the project dir is
    absent, or it holds no transcripts yet.

    Keyed by cwd (not window name) so a caller holding a window *id* can resolve
    collision-free: ``discovery.get_window_cwd_by_id(wid)`` → here.
    """
    if not cwd:
        return None
    proj_dir = (base or CLAUDE_PROJECTS_DIR) / encode_cwd(cwd)
    if not proj_dir.is_dir():
        return None
    found = sorted(
        proj_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return found[0] if found else None


def _resolve_agent_transcript(agent_name: str) -> Path | None:
    """Resolve agent_name → transcript path via its window's live cwd."""
    return transcript_for_cwd(discovery.get_window_cwd(agent_name))


def summary_for_path(path: Path | None) -> dict:
    """`{"recap", "recap_ts", "pr"}` for a resolved transcript path (or all None)."""
    if path is None:
        return {"recap": None, "recap_ts": None, "pr": None}
    rec = _latest_recap_record(path)
    pr = latest_pr(path)
    return {
        "recap": rec.get("content") if rec else None,
        "recap_ts": rec.get("timestamp") if rec else None,
        "pr": pr.to_dict() if pr else None,
    }


def iter_turns(path: Path, include_sidechain: bool = False) -> Iterator[dict]:
    """Distil a transcript JSONL into readable conversation turns (forward order).

    Yields ``{"ts", "role", "text", "tools"}`` for each user/assistant turn:
      - user   → the human/orchestrator prompt (string-content records only;
                 tool-result carrier records, whose content is a list, are skipped).
      - assistant → joined ``text`` blocks; ``tools`` lists any tool_use names on
                    that turn (so a tool-only turn still shows as activity).
    Meta records (``isMeta``) and, by default, sub-agent sidechains
    (``isSidechain``) are skipped so the digest reads as the main conversation,
    not raw JSON.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if o.get("type") not in ("user", "assistant"):
                    continue
                if o.get("isMeta") or (o.get("isSidechain") and not include_sidechain):
                    continue
                msg = o.get("message") or {}
                content = msg.get("content")
                ts = o.get("timestamp")
                if o["type"] == "user":
                    if isinstance(content, str) and content.strip():
                        yield {"ts": ts, "role": "user", "text": content.strip(), "tools": []}
                    continue
                # assistant: join text blocks, collect tool_use names
                texts, tools = [], []
                if isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "text" and b.get("text"):
                            texts.append(b["text"])
                        elif b.get("type") == "tool_use" and b.get("name"):
                            tools.append(b["name"])
                elif isinstance(content, str):
                    texts.append(content)
                text = "\n".join(t.strip() for t in texts if t.strip()).strip()
                if text or tools:
                    yield {"ts": ts, "role": "assistant", "text": text, "tools": tools}
    except OSError:
        return


def latest_context_usage(path: Path) -> dict | None:
    """Context-window token usage from the most recent assistant message.

    Returns ``{"used_tokens": int, "model": str | None}`` where ``used_tokens``
    is ``input + cache_read + cache_creation`` of the last assistant turn — i.e.
    everything resident in the context window on that turn. None if no assistant
    message with a usage block is found. This is the zero-setup fallback for the
    context bar; the statusLine payload (when installed) is authoritative and
    also carries the exact window size, which the transcript does not record.
    """
    rec = latest_record(
        path,
        lambda o: o.get("type") == "assistant"
        and isinstance(o.get("message"), dict)
        and isinstance(o["message"].get("usage"), dict),
    )
    if not rec:
        return None
    u = rec["message"]["usage"]
    used = (
        (u.get("input_tokens") or 0)
        + (u.get("cache_read_input_tokens") or 0)
        + (u.get("cache_creation_input_tokens") or 0)
    )
    return {"used_tokens": used, "model": rec["message"].get("model")}


def agent_context_from_transcript(agent_name: str) -> dict | None:
    """`latest_context_usage` for an agent, resolving its active transcript."""
    path = _resolve_agent_transcript(agent_name)
    if path is None:
        return None
    return latest_context_usage(path)


def agent_transcript_summary(agent_name: str) -> dict:
    """Return `{"recap": str|None, "recap_ts": str|None, "pr": dict|None}`.

    `recap_ts` is the ISO timestamp of the away_summary record so the
    dashboard can show how old a recap is (Claude Code emits these only
    occasionally, so a displayed recap can legitimately lag by hours).
    All fields are None if the transcript can't be found.
    """
    return summary_for_path(_resolve_agent_transcript(agent_name))
