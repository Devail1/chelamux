"""Read recap + PR-link + title records from Claude Code session JSONL transcripts.

Each Claude Code session writes a JSONL transcript at
    $CLAUDE_CONFIG_DIR/projects/<encoded-cwd>/<session-id>.jsonl
(``~/.claude/projects/...`` when ``CLAUDE_CONFIG_DIR`` is unset — Claude Code's default)
where <encoded-cwd> replaces every `/` and `.` in the cwd with `-`.

We want three things per agent:
  - the latest `{"type": "system", "subtype": "away_summary"}.content` (recap)
  - the latest `{"type": "pr-link", ...}` record (last PR opened in-session)
  - the latest `{"type": "ai-title", "aiTitle": ...}` record (Claude's own
    auto-generated session title — distinct from the recap: it names the
    session, the recap summarizes what happened while you were away)

Transcripts grow without bound, so we scan from the tail of the file
backwards block-by-block and stop at the first match (or once we have
scanned `max_scan_bytes`). Public helpers return plain dicts / strings
so callers (the Flask dashboard) don't need to know the file layout.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from chela import discovery

log = logging.getLogger(__name__)


def claude_config_dir() -> Path:
    """Claude Code's config directory — ``$CLAUDE_CONFIG_DIR`` or ``~/.claude``.

    Claude Code relocates its ENTIRE config dir this way, and the transcript tree every
    function below reads (``<config dir>/projects/...``) lives under it. Hardcoding
    ``~/.claude`` here used to mean any adopter who sets ``CLAUDE_CONFIG_DIR`` got NO
    transcript resolution at all — recaps, PR links, ai-titles and the telegram outbound
    relay all silently went dead, with every window's resolver returning None. Mirrored
    (not imported) by :func:`chela.hooks.claude_config_dir`: hooks imports this module, not
    the other way round.
    """
    raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


CLAUDE_PROJECTS_DIR = claude_config_dir() / "projects"

# Cap on how far back we scan a transcript looking for a record. Two MB is
# plenty for the latest recap/PR — they are typically within the last few KB.
DEFAULT_MAX_SCAN_BYTES = 2 * 1024 * 1024
_READ_BLOCK = 64 * 1024


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def encode_cwd(cwd: str) -> str:
    """Encode a cwd into Claude Code's `~/.claude/projects/<dir>` name.

    Claude Code replaces `/`, `.` **and `_`** with `-`, so
    `/home/alice/.chela/worktrees/my_proj/abc` becomes
    `-home-alice--chela-worktrees-my-proj-abc`.

    The `_` was missing until CMX-70, and it was not cosmetic: a directory with an
    underscore in its name (`~/projects/analytics/data_prep`) encoded to a project dir
    that CANNOT EXIST, so every cwd-keyed lookup for that agent — the transcript, and the
    hook correlation that compares this encoding against the slug in a payload's
    `transcript_path` — silently found nothing. Measured against Claude Code 2.1.209, not
    inferred: a headless session run from `…/enc_probe` writes to `…-enc-probe`.
    """
    return cwd.replace("/", "-").replace(".", "-").replace("_", "-")


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


def latest_ai_title(path: Path) -> str | None:
    """Return the latest `{"type": "ai-title"}.aiTitle`, or None.

    Claude Code appends one of these each time it revises the session's
    auto-generated title as the conversation evolves — the LATEST record is
    the current title. Unlike the recap (`latest_recap`, an occasional
    "what happened" summary), this is a short *name* for the session and is
    written far more often, so plain empty-string content is skipped rather
    than treated as "no title yet".
    """
    rec = latest_record(path, lambda o: o.get("type") == "ai-title")
    if not rec:
        return None
    title = rec.get("aiTitle")
    return title.strip() if isinstance(title, str) and title.strip() else None


# ---------------------------------------------------------------------------
# Dashboard-facing helpers
# ---------------------------------------------------------------------------

def _last_record_ts(path: Path) -> datetime | None:
    """Timestamp of the newest JSONL record that carries one, or None.

    Reverse-scans the transcript for the most recent record with a string
    ``timestamp`` field and parses it. This measures the session's *content*
    recency, which is what we want — distinct from the file mtime, which a
    ``/clear`` marker appended to an otherwise-stale pre-clear transcript can
    momentarily bump ahead of the fresh session that is actually current.
    """
    rec = latest_record(path, lambda o: isinstance(o.get("timestamp"), str))
    if not rec:
        return None
    try:
        return datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00"))
    except ValueError:
        return None


def transcript_for_cwd(cwd: str | None, base: Path | None = None) -> Path | None:
    """Resolve a working directory → its active transcript path — the LAST RESORT.

    ⚠️ **A cwd is not a session id, and this function cannot be more right than that.**
    :mod:`chela.sessions` is the authority for "which transcript is this window writing":
    it resolves by ``session_id`` (from the event log's hook-borne records, or the pane's
    own ``claude --resume``) and only falls back to here for a window that has fired no
    hook and was not resumed. This is kept for that case, and for callers that genuinely
    have nothing but a directory. It answers the wrong question in three others — a
    ``--resume`` from a different directory, an agent that ``cd``s, and two windows sharing
    one cwd — see that module for what each of them cost.

    Within its limits: derive the transcript directory from the cwd
    (``~/.claude/projects/<encoded-cwd>/``) and pick the ``*.jsonl`` in it whose newest
    *record* is latest — that is the session most recently writing from that directory.
    Returns None if cwd is empty, the project dir is absent, or it holds no transcripts
    yet.

    We rank by newest-record timestamp rather than file mtime because a
    ``/clear`` starts a new session (new jsonl) while writing a marker into the
    old one: for an instant the pre-clear file is newest by *mtime* even though
    the fresh session is current. Ranking by the last record's timestamp binds
    the right transcript, and ``monitor.py`` re-resolves each poll so it rebinds
    as soon as the new session writes. A candidate with no timestamped record
    yet (e.g. a just-created session) sorts below any that has one, with mtime
    only breaking genuine ties.
    """
    if not cwd:
        return None
    proj_dir = (base or CLAUDE_PROJECTS_DIR) / encode_cwd(cwd)
    if not proj_dir.is_dir():
        return None
    found = list(proj_dir.glob("*.jsonl"))
    if not found:
        return None

    def _key(p: Path) -> tuple[bool, float, float]:
        ts = _last_record_ts(p)
        return (ts is not None, ts.timestamp() if ts is not None else 0.0, p.stat().st_mtime)

    return max(found, key=_key)


def last_assistant_activity_at(path: Path) -> float | None:
    """Epoch seconds of the newest ASSISTANT record in the transcript at ``path``.

    "Did this agent actually do work, and by when?" — the evidence the decisions inbox
    uses to detect a completion it never sampled (see chela.inbox). ASSISTANT, not just
    any record: the orchestrator's own dispatched prompt lands as a *user* record, so
    counting that would read "your prompt arrived" as "the agent replied".

    Content-derived (the record's timestamp), not the file mtime — same reasoning as
    :func:`_last_record_ts`. Sidechains (sub-agent turns) are skipped: a finished task
    always ends with a main-chain assistant turn. None when there is no assistant turn
    yet, the timestamp is unparseable, or ``path`` cannot be read.

    Takes a resolved PATH, not a cwd — the caller (:func:`chela.inbox.did_work_since`)
    already knows which window it is asking about, and a cwd cannot tell two windows in
    one directory apart (see :mod:`chela.sessions`). Resolving here from a bare cwd would
    reopen exactly that hole one call up.
    """
    rec = latest_record(path, lambda o: (
        o.get("type") == "assistant"
        and not o.get("isSidechain")
        and isinstance(o.get("timestamp"), str)
    ))
    if not rec:
        return None
    try:
        return datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def last_assistant_activity(cwd: str | None, base: Path | None = None) -> float | None:
    """:func:`last_assistant_activity_at`, resolved from a cwd — the LAST RESORT.

    ⚠️ Same caveat as :func:`transcript_for_cwd`: a cwd cannot tell two windows sharing
    one directory apart. Kept for callers that genuinely have nothing but a directory;
    a caller that has a window id should resolve via :mod:`chela.sessions` instead (see
    :func:`chela.inbox.did_work_since`).
    """
    path = transcript_for_cwd(cwd, base=base)
    if path is None:
        return None
    return last_assistant_activity_at(path)


def _resolve_agent_transcript(agent_name: str, window_id: str | None = None) -> Path | None:
    """Resolve an agent → its transcript path, by WINDOW when a window id is given.

    Two windows can share one cwd (the same directory launched twice), and a cwd
    guess cannot tell them apart — it hands both of them whichever transcript won
    the "newest record" race, so one window's title/recap/PR bleed into the
    other's (CMX-153; same root cause as CMX-147's Telegram topic collision).
    :mod:`chela.sessions` is the authority for "which transcript is THIS window
    writing" (session id via the event log or ``--resume``, cwd only as a last
    resort, and it REFUSES the cwd guess outright when it can't disambiguate) —
    so a caller that has a window id must hand it over rather than falling back
    to the name→cwd guess here. Only a caller with no window id at all (none
    live, or none looked up) gets the cwd fallback.
    """
    if window_id:
        from chela import sessions
        return sessions.transcript_for_window(window_id)
    return transcript_for_cwd(discovery.get_window_cwd(agent_name))


def summary_for_path(path: Path | None) -> dict:
    """`{"recap", "recap_ts", "pr", "ai_title"}` for a transcript path (or all None)."""
    if path is None:
        return {"recap": None, "recap_ts": None, "pr": None, "ai_title": None}
    rec = _latest_recap_record(path)
    pr = latest_pr(path)
    return {
        "recap": rec.get("content") if rec else None,
        "recap_ts": rec.get("timestamp") if rec else None,
        "pr": pr.to_dict() if pr else None,
        "ai_title": latest_ai_title(path),
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


def agent_context_from_transcript(agent_name: str, window_id: str | None = None) -> dict | None:
    """`latest_context_usage` for an agent, resolving its active transcript.

    Pass `window_id` when the caller has one — see `_resolve_agent_transcript`.
    """
    path = _resolve_agent_transcript(agent_name, window_id)
    if path is None:
        return None
    return latest_context_usage(path)


def agent_transcript_summary(agent_name: str, window_id: str | None = None) -> dict:
    """Return `{"recap": str|None, "recap_ts": str|None, "pr": dict|None, "ai_title": str|None}`.

    `recap_ts` is the ISO timestamp of the away_summary record so the
    dashboard can show how old a recap is (Claude Code emits these only
    occasionally, so a displayed recap can legitimately lag by hours).
    `ai_title` is Claude's own auto-generated title for the session — a
    different record than the recap (see `latest_ai_title`). All fields are
    None if the transcript can't be found. Pass `window_id` when the caller
    has one — see `_resolve_agent_transcript`.
    """
    return summary_for_path(_resolve_agent_transcript(agent_name, window_id))
