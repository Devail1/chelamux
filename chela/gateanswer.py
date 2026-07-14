"""Answer a gate from the phone with **zero keypresses** — the hook returns the answer.

Every gate chela has ever answered was answered by *typing at the terminal*: the relay
worked out which row the TUI's ``❯`` cursor was on, injected that many Down/Up presses,
waited for the selector to settle, and sent Enter. That substrate cannot be made safe for
the shapes we actually send. CMX-32 was a **silent mis-answer** — the human tapped option
3 and the agent was told 2, because Enter raced the arrow moves — and a multi-question or
``multiSelect`` picker multiplies that race by every question. There is no cursor to read
for those shapes at all.

**A ``PermissionRequest`` hook can simply return the answer** (measured 2026-07-13,
Claude Code 2.1.207, and again on 2.1.209 for this change)::

    {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                            "decision": {"behavior": "allow",
                                         "updatedInput": {"questions": [...],
                                                          "answers": {"<question>": "<label>"}}}}}

No keystrokes, in every permission mode including ``auto`` (which does *not* auto-answer a
question — it genuinely blocks on the picker). So for an ``AskUserQuestion`` the answer
travels the same structured channel the *question* did, and the pane is not touched.

The three things that make this safe to run against a live fleet:

**1. The wait is BOUNDED, and it FAILS OPEN.** The hook runs synchronously inside the
agent's process: the moment this endpoint blocks on a human, a live agent is frozen. So
the wait is at most :func:`wait_budget` seconds and then **gives up and returns nothing**
— which leaves the TUI picker exactly where it was, still on screen, still answerable in
tmux or with the nav-row keys. Timing out is *not* a deny (see :func:`answer_permission_request`).

**2. The budget is strictly below the hook's own timeout, which is a MEASURED number.**
Claude Code kills an ``http`` hook that answers later than the ``timeout`` its manifest
declares, and then fails open by itself — so a wait longer than that timeout is a wait
that can never deliver an answer. Measured on 2.1.209 (a hook that never replies, timing
``claude -p`` against a 4.5 s baseline): declared 10 s → blocked 10.2 s; declared 65 s →
blocked ~66 s; declared 130 s → blocked ~133 s. **The declared timeout is honoured
verbatim — there is no 60 s clamp** — and on expiry the turn proceeds unharmed. chela
declares :data:`chela.hooks.GATE_TIMEOUT` for ``PermissionRequest`` alone (the other
events stay on the 2 s budget — ``PreToolUse``/``PostToolUse`` are 78% of the log's
volume and must stay fast) and waits strictly less than it.

**3. A stale answer is REFUSED.** A gate is identified by its ``tool_use_id``, and an
answer carries it. A tap that lands after the gate resolved, after the agent died, or for
a *different* gate in the same topic finds no open gate and is dropped and reported —
never applied to whatever happens to be on screen by then. That is the same failure class
as CMX-32, and it does not get to come back through the new door.

**Why a file, not a queue.** The waiter and the answerer are **different processes**: the
hook POSTs into ``chela-dashboard`` while the Telegram tap lands in ``chela-telegram``. So
the rendezvous is a directory under ``CHELA_DIR`` — the open gate is a file the answering
process can see, and the answer is a file the waiting process polls for. Both writes are
atomic (``os.replace``), the waiter cleans up after itself, and a crashed waiter leaves at
most an expired file that the next read sweeps.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from chela import config

log = logging.getLogger(__name__)

# The only tool answered from here. `ExitPlanMode`'s choices are harness-rendered TUI
# (its `tool_input` carries only the plan), so there is no `answers` map to return and
# nothing to enumerate — it keeps the pane path.
ANSWERABLE_TOOL = "AskUserQuestion"

# A tool_use_id becomes a filename, so it is validated as the token Claude Code emits
# rather than trusted. `../../` in a payload must not be able to walk the filesystem.
_TUID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# How often the blocked hook thread looks for its answer. It is asleep in between; the
# cost of a tighter poll is nothing, and the human's tap should feel instant.
POLL_INTERVAL = 0.15

DEFAULT_WAIT_S = 90.0

# A blocked hook holds one dashboard request thread for the whole budget. Flask serves
# threaded (a thread per request), so what a flood exhausts is memory and file handles,
# not a fixed pool — but "unbounded" is not a plan. Past this many *simultaneously
# waiting* gates, the next one does not wait at all: it fails open to the pane path
# immediately and says so in the log. The agent is never worse off than it was before
# this feature existed — that is the invariant every branch here preserves.
DEFAULT_MAX_WAITS = 8


def wait_budget() -> float:
    """Seconds to hold a blocked agent while waiting for a human — ``CHELA_GATE_WAIT_S``.

    Must stay strictly below :data:`chela.hooks.GATE_TIMEOUT` (the ``timeout`` the plugin
    manifest declares for ``PermissionRequest``), or the answer arrives after Claude Code
    has already killed the hook — a wait that can never deliver. Clamped here rather than
    trusted, because the failure is silent: the human taps, the tap is accepted, and the
    agent never sees it.
    """
    from chela.hooks import GATE_TIMEOUT
    ceiling = max(1.0, GATE_TIMEOUT - 5.0)
    try:
        budget = float(os.environ.get("CHELA_GATE_WAIT_S", DEFAULT_WAIT_S))
    except ValueError:
        budget = DEFAULT_WAIT_S
    if budget <= 0:
        return 0.0                              # explicitly disabled — never wait
    if budget > ceiling:
        log.warning(
            "CHELA_GATE_WAIT_S=%.0fs is above what the PermissionRequest hook timeout "
            "(%ds) can deliver — clamping the wait to %.0fs",
            budget, GATE_TIMEOUT, ceiling,
        )
        return ceiling
    return budget


def max_waits() -> int:
    try:
        return max(1, int(os.environ.get("CHELA_GATE_MAX_WAITS", DEFAULT_MAX_WAITS)))
    except ValueError:
        return DEFAULT_MAX_WAITS


_slots = threading.BoundedSemaphore(DEFAULT_MAX_WAITS)
_slots_size = DEFAULT_MAX_WAITS
_slots_lock = threading.Lock()


def _acquire_slot() -> bool:
    """Take one of the bounded wait slots, or return False (and fail open) at the bound."""
    global _slots, _slots_size
    with _slots_lock:
        if _slots_size != max_waits():           # re-sized by config; rebuild
            _slots_size = max_waits()
            _slots = threading.BoundedSemaphore(_slots_size)
        slots = _slots
    return slots.acquire(blocking=False)


def _release_slot() -> None:
    try:
        _slots.release()
    except ValueError:                           # already at full count — never fatal
        pass


# --- the rendezvous directory ------------------------------------------------------

def gates_dir() -> Path:
    return config.CHELA_DIR / "gates"


def _gate_path(tool_use_id: str) -> Path:
    return gates_dir() / f"{tool_use_id}.gate.json"


def _answer_path(tool_use_id: str) -> Path:
    return gates_dir() / f"{tool_use_id}.answer.json"


def _write_atomic(path: Path, data: dict) -> bool:
    """Write one JSON file atomically. Never raises — a rendezvous we cannot write is a
    gate that is not answerable from the phone, not a stalled agent."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        log.warning("gateanswer: could not write %s", path, exc_info=True)
        return False


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


@dataclass(frozen=True)
class OpenGate:
    """A gate whose hook is **blocked right now**, waiting for an answer.

    This is what makes the zero-keypress path *offerable*: the Telegram side renders
    answer buttons only for a gate that has one of these on disk, because only then is
    there a live hook to hand the answer back to. With no open gate the relay falls back
    to the keystroke path — which is exactly right for a pre-plugin agent, whose gate the
    daemon never saw.
    """

    tool_use_id: str
    wid: str
    questions: list[dict]        # the raw `tool_input["questions"]`, as the asker wrote it
    deadline: float              # epoch seconds; past this the hook has given up
    session_id: str | None = None
    # How long the agent is being held in total. The *card* says this rather than a live
    # countdown, so that the message's content (and therefore the watcher's de-dup
    # signature) is stable across ticks instead of churning an edit every poll.
    budget: float = 0.0

    @property
    def expired(self) -> bool:
        return time.time() >= self.deadline

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.deadline - time.time())


def _as_gate(data: dict | None) -> OpenGate | None:
    if not isinstance(data, dict):
        return None
    tuid = data.get("tool_use_id")
    wid = data.get("wid")
    questions = data.get("questions")
    if not isinstance(tuid, str) or not isinstance(wid, str) or not isinstance(questions, list):
        return None
    try:
        deadline = float(data.get("deadline") or 0)
    except (TypeError, ValueError):
        return None
    session = data.get("session_id")
    try:
        budget = float(data.get("budget") or 0)
    except (TypeError, ValueError):
        budget = 0.0
    return OpenGate(
        tool_use_id=tuid, wid=wid, questions=questions, deadline=deadline,
        session_id=session if isinstance(session, str) else None, budget=budget,
    )


def open_gate(tool_use_id: str) -> OpenGate | None:
    """The open gate with this id, or None — **None if it has expired**.

    Expiry is enforced on the *read*, not on a sweeper, so an answerer can never be handed
    a gate whose hook has already given up. A tap against one of those must be refused and
    reported, not applied to whatever the agent is doing by now.
    """
    if not _TUID_RE.match(tool_use_id or ""):
        return None
    gate = _as_gate(_read_json(_gate_path(tool_use_id)))
    if gate is None or gate.expired:
        return None
    return gate


def open_gate_for_window(wid: str) -> OpenGate | None:
    """The (single) live gate blocking this window, if any."""
    try:
        paths = sorted(gates_dir().glob("*.gate.json"))
    except OSError:
        return None
    for path in paths:
        gate = _as_gate(_read_json(path))
        if gate is None:
            continue
        if gate.expired:
            _unlink(path)                        # the waiter died mid-wait; sweep it
            continue
        if gate.wid == wid:
            return gate
    return None


# --- answering ---------------------------------------------------------------------

def _labels(question: dict) -> list[str]:
    return [
        opt["label"] for opt in (question.get("options") or [])
        if isinstance(opt, dict) and isinstance(opt.get("label"), str)
    ]


def validate_answers(questions: list[dict], answers: dict) -> dict | None:
    """The answers map, normalised — or **None** if it is not one we can hand to an agent.

    Every question must be answered and every label must be one the *asker* offered.
    A ``multiSelect`` question takes a **list** of labels; a single-select takes one
    string (a one-element list is accepted and unwrapped, since that is what a toggle UI
    naturally produces).

    **The map is COMPLETE or it is refused.** A partial map was measured against Claude
    Code 2.1.209: the unanswered question is simply *dropped on the floor* — the agent
    receives an answers map missing that key, believes it has been answered, and carries
    on. It never re-asks. So a half-answered gate would silently discard a question the
    asker thought they had asked, which is the same class of bug as answering the wrong
    option. chela therefore holds the answer until every question has one (the Telegram
    side accumulates the taps) and refuses anything less.
    """
    if not isinstance(answers, dict) or not questions:
        return None
    out: dict[str, str | list[str]] = {}
    for question in questions:
        if not isinstance(question, dict):
            return None
        text = question.get("question")
        if not isinstance(text, str) or text not in answers:
            return None                          # a question with no answer → refuse
        valid = _labels(question)
        picked = answers[text]
        if isinstance(picked, str):
            picked = [picked]
        if not isinstance(picked, list) or not picked:
            return None
        chosen = [p for p in picked if isinstance(p, str) and p in valid]
        if len(chosen) != len(picked):
            return None                          # a label the asker never offered
        if question.get("multiSelect"):
            out[text] = list(dict.fromkeys(chosen))
        else:
            if len(chosen) != 1:
                return None                      # single-select takes exactly one
            out[text] = chosen[0]
    return out


def submit_answer(tool_use_id: str, answers: dict) -> tuple[bool, str]:
    """Deliver an answer to the blocked hook. ``(ok, reason)`` — a refusal is never silent.

    Refused when there is no open gate with that id (it resolved, the agent died, the
    daemon restarted), when the gate has expired (the hook already gave up and the agent
    has moved on), or when the answers do not validate against the questions the asker
    actually sent. In every one of those cases the answer is **dropped and reported** —
    applying it to whatever is on screen now is the CMX-32 mis-answer with extra steps.
    """
    gate = open_gate(tool_use_id)
    if gate is None:
        return False, "that question is no longer waiting for an answer"
    validated = validate_answers(gate.questions, answers)
    if validated is None:
        log.warning("gateanswer: refused an answer for %s — it does not match the "
                    "questions the agent asked", tool_use_id)
        return False, "the answer does not match what the agent asked"
    if not _write_atomic(_answer_path(tool_use_id), {
        "tool_use_id": tool_use_id, "answers": validated, "ts": time.time(),
    }):
        return False, "could not deliver the answer"
    return True, "answered"


def gate_resolved(tool_use_id) -> None:
    """This gate was answered by SOMEONE ELSE — stop holding the agent. Idempotent.

    The gate is now answerable **two ways at once** (CMX-54): a tap on the Telegram option
    buttons, which comes back through this rendezvous, or the D-pad + ``⏎`` on the mirrored
    pane, which answers the TUI directly and never touches these files. In the second case
    the blocked hook is waiting for an answer that will never arrive, and would otherwise
    sit out its whole budget holding one of the :func:`max_waits` slots — a corpse in a slot
    the next gate cannot have.

    So the gate's ``PostToolUse`` — which fires whichever way it was answered, and is the
    only signal that does — tears the rendezvous down here (the dashboard calls this as it
    ingests the event). :func:`wait_for_answer` notices the gate file is gone and gives up
    **immediately**, failing OPEN: it returns no decision, which is exactly right, because
    the tool has already been answered and there is nothing left to decide. It also makes a
    later tap refuse itself — :func:`open_gate` finds nothing, and the human is told the
    question is no longer waiting rather than having it re-aimed at whatever is on screen by
    now.
    """
    if not isinstance(tool_use_id, str) or not _TUID_RE.match(tool_use_id):
        return
    path = _gate_path(tool_use_id)
    if path.exists():
        log.info("gateanswer: %s was answered at the terminal — releasing the held hook",
                 tool_use_id)
        _unlink(path)


def wait_for_answer(tool_use_id: str, budget: float,
                    poll: float = POLL_INTERVAL,
                    now=time.monotonic, sleep=time.sleep,
                    resolved=None) -> dict | None:
    """Block until an answer lands for this gate, or the budget runs out (→ ``None``).

    ``None`` means **fail open**, never deny: the caller returns an empty hook response and
    the picker stays exactly as it was.

    ``resolved()`` (when given) is the early exit for a gate that was answered **at the
    terminal** while we were holding it (:func:`gate_resolved`): it is checked each poll and
    a True gives up at once, releasing the wait slot instead of holding it for the rest of
    the budget. It can only ever make this function return **sooner**, and only ever with
    the same fail-open ``None`` — no branch here can deny anything, and none was added.
    """
    deadline = now() + budget
    path = _answer_path(tool_use_id)
    while True:
        data = _read_json(path)
        if isinstance(data, dict) and isinstance(data.get("answers"), dict):
            return data["answers"]
        if resolved is not None and resolved():
            log.info("gateanswer: %s resolved without us — the hook returns now, not in "
                     "%.0fs", tool_use_id, budget)
            return None
        if now() >= deadline:
            return None
        sleep(min(poll, max(0.0, deadline - now())))


def close_gate(tool_use_id: str) -> None:
    """Tear the rendezvous down. Idempotent — the waiter always calls it, win or lose."""
    _unlink(_gate_path(tool_use_id))
    _unlink(_answer_path(tool_use_id))


# --- the decision the hook returns --------------------------------------------------

def decision(questions: list[dict], answers: dict) -> dict:
    """The ``PermissionRequest`` hook body that ANSWERS an ``AskUserQuestion``.

    ``behavior: "allow"`` with an ``updatedInput`` carrying the original ``questions`` and
    an ``answers`` map keyed by the **question string** (measured — this is the shape
    Claude Code accepts, in every permission mode).
    """
    return {"hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {
            "behavior": "allow",
            "updatedInput": {"questions": questions, "answers": answers},
        },
    }}


def _bound_windows() -> set[str]:
    """The windows a human is actually watching — read fresh from the bindings file."""
    from chela.telegram.bindings import BindingRegistry, default_bindings_path
    try:
        return set(BindingRegistry.load(default_bindings_path()).windows())
    except Exception:                            # noqa: BLE001 — no bindings ⇒ nobody watching
        log.debug("gateanswer: could not read the bindings", exc_info=True)
        return set()


def answer_permission_request(body: dict, *, wid_for=None, pending=None,
                              bound=None) -> dict | None:
    """Hold the blocked agent for up to :func:`wait_budget` seconds, then answer — or not.

    ``None`` is the **fail-open** answer and the one every uncertain branch takes: the
    endpoint then returns ``{}``, Claude Code carries on exactly as it does today, and the
    picker is still on screen for the human to answer in tmux or with the nav keys. ⛔
    None of these branches may ever be "hardened" into a **deny**: a deny would destroy an
    agent's work because a human was slow, and this feature's entire promise is that the
    run is *never worse off* than before it existed.

    It declines to wait — instantly, with no cost to the agent — when:

    * the tool is not an :data:`ANSWERABLE_TOOL` (a Bash gate has no ``answers`` map);
    * the window cannot be identified (:func:`chela.hooks.wid_for_session`), or has **no
      bound topic**: nobody can see this question, so nobody can answer it, and blocking a
      live agent on a human who was never shown the question is the worst branch here;
    * the log holds no matching unresolved ``PreToolUse`` — that event is the only carrier
      of the ``tool_use_id`` (``PermissionRequest`` has none), so without it an answer
      could not be tied back to *this* gate;
    * the payload's questions differ from the pending gate's — two calls are in flight and
      we cannot prove which one is blocked;
    * every wait slot is taken (:func:`max_waits`).
    """
    from chela import hooks
    from chela.telegram.hookgate import parse_questions, pending_gate

    if str(body.get("tool_name") or "") != ANSWERABLE_TOOL:
        return None
    tool_input = body.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions:
        return None

    budget = wait_budget()
    if budget <= 0:
        return None

    session_id = body.get("session_id")
    session_id = session_id if isinstance(session_id, str) else None
    transcript = body.get("transcript_path")
    resolve = wid_for or hooks.wid_for_session
    wid = resolve(session_id, transcript if isinstance(transcript, str) else None)
    if wid is None:
        log.info("gateanswer: a question arrived from a session we cannot place in a "
                 "window — leaving it to the pane")
        return None
    if wid not in (bound() if bound else _bound_windows()):
        log.info("gateanswer: %s has no bound topic — nobody would see this question, so "
                 "the agent is not held", wid)
        return None

    # `PermissionRequest` carries no `tool_use_id`, so the gate's identity comes from the
    # `PreToolUse` that fired for the same call a moment earlier. Its questions must be
    # the ones we were just handed, or we are looking at a different call.
    gate = (pending or pending_gate)(wid)
    if gate is None or gate.tool != ANSWERABLE_TOOL:
        log.warning("gateanswer: no pending %s in the log for %s — the answer could not "
                    "be tied to this gate, so the pane keeps it", ANSWERABLE_TOOL, wid)
        return None
    if gate.questions != parse_questions(tool_input):
        log.warning("gateanswer: the pending gate on %s asks something else — refusing to "
                    "answer a gate we cannot identify", wid)
        return None

    if not _acquire_slot():
        log.warning("gateanswer: %d gates are already waiting (CHELA_GATE_MAX_WAITS) — "
                    "%s falls back to the pane rather than holding another thread",
                    max_waits(), wid)
        return None
    try:
        _write_atomic(_gate_path(gate.tool_use_id), {
            "tool_use_id": gate.tool_use_id,
            "wid": wid,
            "session_id": session_id,
            "questions": questions,
            "deadline": time.time() + budget,
            "budget": budget,
            "ts": time.time(),
        })
        log.info("gateanswer: holding %s on %s for up to %.0fs", gate.tool_use_id, wid,
                 budget)
        # …but only until it is answered, by whichever route. A ⏎ on the mirrored pane
        # answers the TUI directly and never comes through here; its `PostToolUse` unlinks
        # the gate file (:func:`gate_resolved`) and this wait ends on the next poll rather
        # than holding a slot for the rest of the budget.
        gate_path = _gate_path(gate.tool_use_id)
        answers = wait_for_answer(
            gate.tool_use_id, budget, resolved=lambda: not gate_path.exists())
    finally:
        close_gate(gate.tool_use_id)
        _release_slot()

    if answers is None:
        log.info("gateanswer: nobody answered %s within %.0fs — failing OPEN (the picker "
                 "is untouched and still answerable in the terminal)", gate.tool_use_id,
                 budget)
        return None
    log.info("gateanswer: answered %s from Telegram with no keystrokes", gate.tool_use_id)
    return decision(questions, answers)
