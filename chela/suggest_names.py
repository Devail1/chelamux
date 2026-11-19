"""Sidebar "Suggest names": bulk name + title proposals from each session's real content (CMX-434).

Two different things get renamed here, and the dialog edits both:

* **Name**: the tmux window name. The dialog applies it through the existing
  ``POST /api/agents/<wid>/rename`` (which marks it manual, CMX-62, and the Telegram topic
  follows it); this module only proposes it (the dialog checks an edited name inline).
* **Title**: Claude Code's own session title, set by typing ``/rename <title>`` into the
  session; Claude Code records it in the transcript as ``customTitle``.

**Suggestions come from what the session is doing NOW**: its latest recap (what
``chela peek`` shows), else its last few user and assistant turns, never its first prompt
(the stale-title problem this exists to fix). ONE headless Haiku call
(:class:`chela.judge_eval.runner.ClaudeCLIRunner`, no tools) proposes every row; code then
forces each proposal into shape (:func:`clean_name`, :func:`clean_title`) and makes the names
unique across every live window (:func:`unique_names`). A proposal is only a proposal: nothing
here applies a name, and a title is applied only by :func:`apply_title`, which the dialog calls.

**Typing ``/rename`` is the dangerous half.** It goes into a live pane, so
:func:`deliver_title` types it ONLY when all of these hold, each read fresh:

1. a Claude process runs in the pane (``claude_pid``). The lesson from the manual run that
   led to this: a pane that looked like a session was a bare ``bash``, and ``/rename`` was
   typed into the shell;
2. its native status is ``idle`` (busy, waiting or unknown: not now);
3. its pane shows no dialog or prompt (:func:`dialog_showing`: the folder-trust prompt, a
   permission prompt, a numbered menu), whose keys our text would answer;
4. its input line is empty (:func:`chela.rc_rename.prompt_is_empty`, SGR-aware: a ghost
   suggestion is empty, a typed draft is not).

A title that cannot go now is QUEUED on the window (:data:`PENDING_OPTION`, a tmux user
option like ``@chela_manual_name``: it dies with the window and both the dashboard and the
daemon see it), shown as pending, and the daemon retries it every tick
(:func:`flush_pending`). A pane with no Claude in it is never queued: it is refused.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from chela import config

log = logging.getLogger(__name__)

PENDING_OPTION = "@chela_title_pending"

NAME_MAX = 20
TITLE_MAX = 40
# A title we are willing to type after `/rename`: printable, one line, no escape bytes.
_TITLE_RE = re.compile(r"\A[^\x00-\x1f\x7f-\x9f]{1,40}\Z")

# How much of a session the model reads: the recap, else the last turns, each capped.
TAIL_TURNS = 6
TURN_CHARS = 400
CONTENT_CHARS = 2000

MODEL = "haiku"
MODEL_BUDGET_USD = 0.50
MODEL_TIMEOUT_S = 180

SCHEMA = {"type": "object", "required": ["suggestions"], "properties": {"suggestions": {
    "type": "array", "items": {"type": "object", "required": ["wid", "name", "title"],
                               "properties": {"wid": {"type": "string"},
                                              "name": {"type": "string"},
                                              "title": {"type": "string"}}}}}}

PROMPT = f"""You name terminal sessions of a coding assistant by what each one is doing NOW.
Below is one entry per session: its id (`wid`) and what it is doing (its latest summary, or
its last few turns). For each, propose:
- name: a short slug, at most {NAME_MAX} characters, only lowercase letters, digits and '-'
  (e.g. "tipstack-api", "fix-login-bug")
- title: a short human title, at most {TITLE_MAX} characters (e.g. "Tipstack API rate limits")
Name the CURRENT work, not how the session started. Return one suggestion per wid, copying
`wid` exactly. Return JSON only.

"""


# ---------------------------------------------------------------------------
# Shaping and checking a proposal
# ---------------------------------------------------------------------------

def clean_name(raw) -> str:
    """``raw`` forced into the name rule: lowercase, ``[a-z0-9-]``, no doubled or edge
    dashes, at most :data:`NAME_MAX` chars. ``""`` when nothing usable is left."""
    s = re.sub(r"[^a-z0-9]+", "-", str(raw or "").lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:NAME_MAX].rstrip("-")


def clean_title(raw) -> str:
    """``raw`` as one line of at most :data:`TITLE_MAX` printable chars (``""`` if none)."""
    s = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(raw or ""))
    s = " ".join(s.split())
    return s[:TITLE_MAX].rstrip()


def unique_names(proposed: dict[str, str], windows: dict[str, str]) -> dict[str, str]:
    """Make each proposed name unique across every live window.

    ``proposed`` is ``{wid: name}`` for the rows asked about; ``windows`` is every live
    window ``{wid: current name}``. A name is free when no OTHER window has it now and no
    earlier row took it: another window's current name is never proposed, even when that
    window is being renamed too, because the dialog applies renames one at a time and the
    rename API refuses a name another window holds at that moment. A row may keep its own
    name. Each row takes the first free of ``name``, ``name-2``, ``name-3``… (cut to fit
    :data:`NAME_MAX`). Rows are settled in wid order so the result is stable.
    """
    assigned: set[str] = set()
    out: dict[str, str] = {}
    for wid in sorted(proposed, key=_wid_key):
        def free(name: str, wid: str = wid) -> bool:
            return name not in assigned and not any(
                n == name and w != wid for w, n in windows.items())
        base = proposed[wid]
        name, n = base, 1
        while not free(name):
            n += 1
            suffix = f"-{n}"
            name = base[:NAME_MAX - len(suffix)].rstrip("-") + suffix
        assigned.add(name)
        out[wid] = name
    return out


def _wid_key(wid: str):
    digits = wid.lstrip("@")
    return (0, int(digits)) if digits.isdigit() else (1, wid)


# ---------------------------------------------------------------------------
# What a session is doing now
# ---------------------------------------------------------------------------

def content_for(path: Path | None) -> str | None:
    """What the model reads for one session: its latest recap, else its last
    :data:`TAIL_TURNS` user and assistant turns. None when the transcript says nothing."""
    from chela import transcripts

    if path is None:
        return None
    recap = transcripts.latest_recap(path)
    if recap and recap.strip():
        return recap.strip()[:CONTENT_CHARS]
    turns = [t for t in transcripts.iter_turns(path) if t["text"]][-TAIL_TURNS:]
    if not turns:
        return None
    lines = [f"{t['role']}: {' '.join(t['text'].split())[:TURN_CHARS]}" for t in turns]
    return "\n".join(lines)[-CONTENT_CHARS:]


def current_title(path: Path | None) -> str | None:
    """The session's title as Claude Code shows it: its ``customTitle`` (``/rename``),
    else its ``ai-title``. None when it has neither."""
    from chela import transcripts

    if path is None:
        return None
    return transcripts.latest_custom_title(path) or transcripts.latest_ai_title(path)


# ---------------------------------------------------------------------------
# The proposals
# ---------------------------------------------------------------------------

def ask_model(entries: list[dict], runner=None) -> tuple[dict[str, dict], str | None]:
    """The one model call: ``({wid: {"name", "title"}}, error)``; exactly one is empty/None.

    ``runner`` is the headless runner (:class:`ClaudeCLIRunner` on Haiku, no tools, by
    default). A wid the model invents is dropped.
    """
    import json
    import tempfile

    if runner is None:
        from chela.judge_eval.runner import ClaudeCLIRunner
        runner = ClaudeCLIRunner(MODEL, max_budget_usd=MODEL_BUDGET_USD, timeout=MODEL_TIMEOUT_S)
    prompt = PROMPT + json.dumps(entries, indent=1)
    # A scratch cwd: the call reads no repo and leaves nothing behind.
    with tempfile.TemporaryDirectory(prefix="chela-suggest-") as cwd:
        result = runner.run(prompt, cwd=cwd, schema=SCHEMA, tools=())
    raw = (result.structured or {}).get("suggestions")
    if result.error or not isinstance(raw, list):
        return {}, result.error or "the model returned no suggestions list"
    asked = {e["wid"] for e in entries}
    out: dict[str, dict] = {}
    for s in raw:
        if isinstance(s, dict) and s.get("wid") in asked:
            out[s["wid"]] = {"name": s.get("name"), "title": s.get("title")}
    return out, None


def suggest(wids: list[str], runner=None) -> dict:
    """Proposals for the live Claude sessions among ``wids``.

    ``{"rows": [...], "error": str | None}``. Each row: ``wid``, ``name`` (current),
    ``manual_name``, ``title`` (current, or None), ``title_pending``, ``content`` (whether
    the session had anything to read), ``suggested_name``, ``suggested_title``, ``note``.
    A wid that is not a live window, or has no Claude in it, gets no row. With no content or
    no answer for a row, its suggestion is its current value and ``note`` says why; a failed
    model call is ``error``, and every suggestion is then the current value.
    """
    from chela import agent_manager, discovery, sessions

    windows = discovery.get_windows_by_id()
    pending = pending_titles()
    rows, entries = [], []
    for wid in wids:
        if wid not in windows or agent_manager.claude_pid(wid) is None:
            continue
        path = sessions.transcript_for_window(wid)
        text = content_for(path)
        rows.append({"wid": wid, "name": windows[wid],
                     "manual_name": agent_manager.is_manual_name(wid),
                     "title": current_title(path), "title_pending": pending.get(wid),
                     "content": text is not None})
        if text is not None:
            entries.append({"wid": wid, "doing": text})

    answers, error = ask_model(entries, runner) if entries else ({}, None)
    proposed: dict[str, str] = {}
    for r in rows:
        a = answers.get(r["wid"]) or {}
        name, title = clean_name(a.get("name")), clean_title(a.get("title"))
        r["note"] = (None if name and title else
                     "nothing to read in this session yet" if not r["content"] else
                     "no suggestion" if not error else "suggestion failed")
        proposed[r["wid"]] = name or clean_name(r["name"]) or "session"
        r["suggested_title"] = title or r["title"] or ""
    final = unique_names(proposed, windows)
    for r in rows:
        r["suggested_name"] = final[r["wid"]]
    return {"rows": rows, "error": error}


# ---------------------------------------------------------------------------
# Applying a title: typed into a live pane, so gated
# ---------------------------------------------------------------------------

# Claude Code's folder-trust prompt (old and new wording), its permission prompts, and any
# numbered selection menu (the highlighted `❯ 1.` row, its "Enter to confirm" footer).
_DIALOG_RES = (
    ("trust", re.compile(r"trust (this|the files in this) folder|Quick safety check", re.I)),
    ("permission", re.compile(r"Do you want to (proceed|make this edit|create|allow|run)\b", re.I)),
    ("menu", re.compile(r"^\s*[❯›>]\s*\d+\.\s", re.M)),
    ("menu", re.compile(r"Enter to (confirm|select)|Esc to cancel", re.I)),
)


def dialog_showing(plain_pane: str) -> str | None:
    """Which dialog ``plain_pane`` (escape-free text) shows: ``"trust"``, ``"permission"``,
    ``"menu"``, or None for none. Keys typed now would answer it, not reach the prompt."""
    for kind, rx in _DIALOG_RES:
        if rx.search(plain_pane):
            return kind
    return None


def _target(wid: str) -> str:
    return f"{config.current_session()}:{wid}"


def _set_pending(wid: str, title: str | None) -> None:
    cmd = (["tmux", "set-window-option", "-t", _target(wid), PENDING_OPTION, title]
           if title is not None else
           ["tmux", "set-window-option", "-u", "-t", _target(wid), PENDING_OPTION])
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("suggest_names: set %s on %s failed: %s", PENDING_OPTION, wid, e)


def pending_titles() -> dict[str, str]:
    """``{wid: queued title}`` for every window with a title waiting. ``{}`` when tmux
    can't say (a row then simply reads not pending)."""
    try:
        out = subprocess.run(
            ["tmux", "list-windows", "-t", config.current_session(), "-F",
             f"#{{window_id}}\t#{{{PENDING_OPTION}}}"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("suggest_names: list-windows failed: %s", e)
        return {}
    if out.returncode != 0:
        return {}
    pending = {}
    for line in out.stdout.splitlines():
        wid, _, title = line.partition("\t")
        if wid and title:
            pending[wid] = title
    return pending


def pending_title(wid: str) -> str | None:
    """The title queued on ``wid``, read off the shared pane snapshot (no spawn on the polled
    ``/api/agents``: :attr:`chela.sessions.Pane.title_pending`). None when nothing waits or
    the snapshot has no such window."""
    from chela import probecache, sessions

    batch = probecache.active()
    pane = batch.pane(wid) if batch is not None else sessions.panes().get(wid)
    return pane.title_pending if pane is not None else None


def _status(cpid: int) -> str | None:
    from chela import agent_manager
    entry = agent_manager.session_entry(cpid, agent_manager.session_status_map())
    return entry["status"] if entry else None


def deliver_title(wid: str, title: str) -> str:
    """Type ``/rename <title>`` into ``wid`` iff it is safe NOW (see the module doc).

    Returns ``"sent"``, ``"no-claude"`` (no Claude process in the pane: never typed into,
    and the queue is dropped), or why it waits: ``"busy"``, ``"waiting"`` / ``"unknown"``
    (the native status), ``"unreadable"`` (no pane capture), ``"dialog"``, ``"draft"``,
    ``"failed"`` (the send itself). Only ``sent`` and ``no-claude`` clear
    :data:`PENDING_OPTION`.
    """
    from chela import agent_manager, dispatcher, messenger, rc_rename

    cpid = agent_manager.claude_pid(wid)
    if cpid is None:
        log.warning("suggest_names: %s has no Claude process; /rename %r NOT typed", wid, title)
        _set_pending(wid, None)
        return "no-claude"
    status = _status(cpid)
    if status != "idle":
        return status if status in ("busy", "waiting") else "unknown"
    pane = messenger.capture_pane(wid, ansi=True)
    if not pane:
        return "unreadable"
    plain = dispatcher._drop_ghost_suggestion(pane)
    dialog = dialog_showing(plain)
    if dialog:
        log.info("suggest_names: %s shows a %s; /rename %r stays queued", wid, dialog, title)
        return "dialog"
    if not rc_rename.prompt_is_empty(pane):
        return "draft"
    if not messenger.send_tmux(wid, f"/rename {title}", interrupt=False):
        log.warning("suggest_names: /rename %r to %s failed to send; stays queued", title, wid)
        return "failed"
    _set_pending(wid, None)
    log.info("suggest_names: typed /rename %r into %s", title, wid)
    return "sent"


NO_CLAUDE = "no Claude session runs in this pane"
BAD_TITLE = f"title must be 1-{TITLE_MAX} printable characters"


def apply_title(wid: str, title: str) -> dict:
    """The dialog's title apply: queue ``title`` on ``wid`` and try it now.

    ``{"ok", "state", "reason"}``: ``state`` is ``"applied"`` (typed now), ``"pending"``
    (queued; ``reason`` says what it waits for) or ``"refused"`` (an unusable title, or a
    pane with no Claude in it; nothing queued).
    """
    from chela import agent_manager

    title = clean_title(title)
    if not _TITLE_RE.match(title):
        return {"ok": False, "state": "refused", "reason": BAD_TITLE}
    if agent_manager.claude_pid(wid) is None:
        return {"ok": False, "state": "refused", "reason": NO_CLAUDE}
    _set_pending(wid, title)
    outcome = deliver_title(wid, title)
    if outcome == "sent":
        return {"ok": True, "state": "applied", "reason": None}
    if outcome == "no-claude":
        return {"ok": False, "state": "refused", "reason": NO_CLAUDE}
    return {"ok": True, "state": "pending", "reason": outcome}


def flush_pending() -> dict[str, str]:
    """Retry every queued title once: the daemon's per-tick call. ``{wid: outcome}``."""
    return {wid: deliver_title(wid, title) for wid, title in pending_titles().items()}
