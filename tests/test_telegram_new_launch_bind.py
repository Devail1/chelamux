"""The load-bearing claim of ``/new``, proved against a REAL tmux server: launch AND bind.

Everything in :mod:`tests.test_telegram_newsession` drives a STUB launcher — good for the
folder-browser UI and the chat-gate wiring, but a stub reproduces the *fixture*, not the
*feature*. The feature is "start a session from Telegram and it gets a topic", and that spans
two subsystems the unit tests never touch together: the real tmux window-open
(:func:`chela.spawn.spawn_window`, via :func:`chela.telegram.newsession.launch_claude_window`)
and the auto-topics reconcile that binds it (:func:`chela.telegram.reconcile.reconcile_bindings`).
So this module opens a real window on a real (scratch-socket) tmux server and drives the real
reconcile over it.

The sharp edge is the **lazy-bind interaction (CMX-73)**. The reconcile deliberately withholds
a topic from a DISPATCHER-owned window until it blocks on a human, so a fleet of short-lived
workers can't spam the forum. A ``/new`` window is NOT dispatcher-owned — it has no run row —
so it must be bound **promptly**, exactly like any hand-started agent, never suppressed. That
is not something a docstring can assert; :func:`test_a_new_window_is_bound_promptly_and_stamped`
proves it end to end, and the trio in
:func:`test_lazy_bind_governs_dispatched_windows_only_not_new_windows` proves the suppression
mechanism is real (so the prompt bind is meaningful, not vacuous).

⛔ **``tmux -L <scratch socket>`` ONLY.** A bare ``kill-server`` has taken the developer's live
fleet down three times. Every tmux call here — ours and the product code's — is pinned to a
private socket by a PATH shim (:func:`tmux`), and the scratch socket name is asserted before
any destructive call. Nothing in this file may ever run ``kill-server`` without ``-L``. This is
the same isolation ``tests/test_epoch_live.py`` and ``tests/test_terminals_selfheal.py`` use.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
import uuid

import pytest

from chela import agent_manager, discovery, epoch
from chela.telegram import (
    BindingRegistry,
    blocked_on_human,
    dispatched_window_ids,
    live_agent_windows,
    reconcile_bindings,
)
from chela.telegram.newsession import launch_claude_window

TMUX_BIN = shutil.which("tmux")
pytestmark = pytest.mark.skipif(TMUX_BIN is None, reason="tmux not installed")

CHAT = 777


def _assert_scratch(sock: str) -> None:
    """Hard guard: never operate on the default socket (that is the live fleet)."""
    assert sock.startswith("chelatest-") and sock != "default", f"unsafe tmux socket: {sock}"


class _StubTopicApi:
    """The Bot API stand-in: records creates/closes, hands out canned thread ids in order.

    Same shape :mod:`tests.test_telegram_reconcile` uses — the reconcile only needs
    ``create_topic`` / ``close_topic`` / ``rename_topic``, so no live Telegram is touched.
    """

    def __init__(self, threads=("100", "101", "102")):
        self._threads = list(threads)
        self.created: list[str] = []
        self.closed: list[str] = []
        self.renamed: list[tuple[str, str]] = []

    def create_topic(self, name: str):
        self.created.append(name)
        return self._threads.pop(0) if self._threads else None

    def rename_topic(self, thread_id, name: str):
        self.renamed.append((str(thread_id), name))
        return True

    def close_topic(self, thread_id):
        self.closed.append(str(thread_id))
        return True


@pytest.fixture
def tmux(tmp_path, monkeypatch):
    """A scratch tmux server the PRODUCT CODE finds when it shells out to a bare ``tmux``.

    ``spawn``, ``discovery``, ``agent_manager``, ``epoch`` all run a bare ``tmux`` with no
    ``-L``, so the only way to make them talk to our server instead of the developer's is to
    own the PATH they resolve it on. The shim re-execs the real binary with ``-L <sock>`` —
    per invocation, so it cannot be dropped the way a ``$TMUX_TMPDIR`` can. The session name
    is ``config.current_session()`` — the ``chela-tests-no-such-session`` conftest pins — so
    the product code's own targeting lands on our windows.

    A fake ``claude`` on the PATH too: :func:`chela.agent_manager.window_type` classifies a
    window as an agent only when a ``claude`` process is actually running in it, and only agent
    windows get topics. The fake is a plain ``sleep`` script *named* ``claude`` (so
    ``pgrep -f claude`` finds it), pointed at by ``DEFAULT_LAUNCH_CMD`` — a real child process,
    so the classification the reconcile depends on is real, not stubbed.
    """
    sock = f"chelatest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    _assert_scratch(sock)

    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "tmux").write_text(f'#!/bin/sh\nexec {TMUX_BIN} -L {sock} "$@"\n')
    (shim / "tmux").chmod(0o755)

    # A fake `claude`: a bare `sleep` (NOT exec'd, so the process keeps `claude` in its
    # command line for pgrep). Absolute path, so the send-keys launch needs no PATH lookup.
    fake_claude = shim / "claude"
    fake_claude.write_text("#!/bin/sh\nsleep 300\n")
    fake_claude.chmod(0o755)
    monkeypatch.setattr(agent_manager, "DEFAULT_LAUNCH_CMD", str(fake_claude))

    # Belt-and-braces (the `-L` is the guarantee): a short scratch socket dir, so a bare tmux
    # that somehow escaped the shim still cannot resolve the default socket. NOT under tmp_path
    # — a unix socket path is capped at ~108 chars and pytest's tmp nests deep.
    tmuxdir = tempfile.mkdtemp(prefix="cmx83", dir="/tmp")
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TMUX_TMPDIR", tmuxdir)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)  # never inherit this agent's own pane

    yield sock

    # Teardown: kill ONLY the scratch server, and wait for it to actually die (tmux tears down
    # asynchronously). Scoped to `-L <sock>` — never a bare kill-server.
    _assert_scratch(sock)
    subprocess.run([TMUX_BIN, "-L", sock, "kill-server"],
                   capture_output=True, text=True)
    deadline = time.time() + 10
    while subprocess.run([TMUX_BIN, "-L", sock, "list-sessions"],
                         capture_output=True, text=True).returncode == 0:
        if time.time() >= deadline:
            break
        time.sleep(0.1)
    shutil.rmtree(tmuxdir, ignore_errors=True)


def _wait_for_agent(wid: str, deadline_s: float = 15.0) -> None:
    """Block until tmux reports a ``claude`` running in ``wid`` (the fake sleep starting up).

    ``send-keys`` is asynchronous — the shell has to draw its prompt and fork the command — so
    the window is a bare shell for a beat after launch. The reconcile only ever runs against
    the fleet as it finds it, so waiting here is faithful, not a fudge: it is the same "a
    moment later" the daemon's next tick would see.
    """
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        if agent_manager.window_type(wid) == "claude":
            return
        time.sleep(0.1)
    raise AssertionError(f"claude never came up in {wid} (window_type stayed non-agent)")


# --------------------------------------------------------------------------
# Step 1: /new really opens a window, at the chosen cwd.
# --------------------------------------------------------------------------

def test_new_command_opens_a_real_window_at_the_chosen_cwd(tmux, tmp_path):
    """Drive the REAL ``/new <path>`` handler (no stub launcher) against a real tmux server.

    Proves the first half of the feature: a ``/new`` really lands a window in the chela
    session, opened in the directory the operator named — its pane's cwd is that directory.
    """
    pytest.importorskip("telegram")
    from telegram.ext import CommandHandler

    from chela.telegram.inbound import TopicRouter, build_application

    proj = tmp_path / "myproject"
    proj.mkdir()

    # No launch_session injected → the handler uses the real launch_claude_window → spawn.
    app = build_application("123:fake-token", TopicRouter(str(CHAT), "@3", "4"))
    hs = [h for group in app.handlers.values() for h in group]
    on_new = next(h.callback for h in hs
                  if isinstance(h, CommandHandler) and "new" in h.commands)

    update = _CmdUpdate(f"/new {proj}")
    asyncio.run(on_new(update, _Ctx()))

    reply = update.message.replies[-1][0]
    assert "Started a Claude session" in reply

    # A REAL window now exists in the session, opened at the project dir.
    want = os.path.realpath(proj)
    cwds = {wid: discovery.get_window_cwd_by_id(wid)
            for wid in discovery.get_windows_by_id()}
    assert want in {os.path.realpath(c) for c in cwds.values() if c}, \
        f"no live window opened at {want}; saw {cwds}"


# --------------------------------------------------------------------------
# Step 2 + 3: the reconcile binds it PROMPTLY, epoch-stamped — lazy-bind does NOT swallow it.
# --------------------------------------------------------------------------

def test_a_new_window_is_bound_promptly_and_stamped(tmux, tmp_path):
    """Launch a ``/new`` window for real, then drive the real reconcile: it gets a topic.

    The whole feature, end to end: :func:`launch_claude_window` opens the window,
    :func:`live_agent_windows` classifies it as an agent (a real ``claude`` child is running
    in it), and :func:`reconcile_bindings` — run exactly as the daemon's ``_reconcile_loop``
    runs it, with the live epoch and the real dispatched-set computation — binds it to a topic
    and STAMPS that binding with the current tmux epoch (CMX-77).

    The binding is prompt because a ``/new`` window is not dispatcher-owned: it has no run row,
    so :func:`dispatched_window_ids` never names it, so lazy-bind never applies. That absence
    is asserted directly, then shown to be what the reconcile acts on.
    """
    proj = tmp_path / "chelamux"
    proj.mkdir()

    wid, err = launch_claude_window(str(proj))
    assert err is None and wid and wid.startswith("@"), f"launch failed: {err!r}"
    _wait_for_agent(wid)

    live, agents = live_agent_windows()
    assert wid in agents, "the /new window is not classified as an agent window"

    now = epoch.current()
    assert now, "a running scratch server must have an epoch"

    # A /new window has NO run row. Prove it is not dispatcher-owned even when the runs table
    # holds an in-flight dispatched agent (a different, fabricated window id): the /new wid is
    # simply absent from the owned set, so lazy-bind can never gate it.
    runs = [{"window_id": "@999", "status": "running",
             "window_name": "cmx-1", "window_epoch": now}]
    dispatched = dispatched_window_ids(runs=runs, live_windows=live, now_epoch=now)
    assert wid not in dispatched, "a /new window must never read as dispatcher-owned"

    reg = BindingRegistry(str(CHAT))
    api = _StubTopicApi()
    changed = reconcile_bindings(
        reg, live, agents, api,
        cwd_for=discovery.get_window_cwd_by_id,
        dispatched=dispatched,
        gate_for=blocked_on_human,
        now_epoch=now,
    )

    assert changed, "the reconcile made no change — the new window went unbound"
    thread = reg.thread_for_window(wid)
    assert thread is not None, "the /new window did NOT get a topic — lazy-bind swallowed it"
    assert reg.epoch_for(wid) == now, "the binding was not stamped with the live tmux epoch"
    # A bonus that it went through the real naming path: the topic is named after the project.
    assert "chelamux" in api.created


def test_lazy_bind_governs_dispatched_windows_only_not_new_windows(tmux, tmp_path):
    """The contrast that makes the prompt bind meaningful: the SAME window, three reconciles.

    Using one real ``/new`` window id, driven through :func:`reconcile_bindings` three ways:

    * **not dispatched** (the ``/new`` reality) → bound. This is what a ``/new`` window is.
    * **dispatched, not blocked** → NOT bound. Proves lazy-bind genuinely suppresses — so the
      bind above is because ``/new`` is dispatcher-free, not because the reconcile binds
      everything it sees.
    * **dispatched, blocked on a human** → bound. Proves the suppression is lazy, not a
      permanent exclusion (CMX-73/CMX-81).
    """
    proj = tmp_path / "work"
    proj.mkdir()
    wid, err = launch_claude_window(str(proj))
    assert err is None and wid, f"launch failed: {err!r}"
    _wait_for_agent(wid)

    live, agents = live_agent_windows()
    now = epoch.current()
    assert wid in agents and now

    # 1. Not dispatched — a /new window. Bound.
    reg = BindingRegistry(str(CHAT))
    reconcile_bindings(reg, live, agents, _StubTopicApi(),
                       dispatched=set(), gate_for=lambda _w: None, now_epoch=now)
    assert reg.thread_for_window(wid) is not None, \
        "a non-dispatched window must be bound promptly"

    # 2. Dispatched and NOT blocked — the lazy-bind suppression the /new path must escape.
    reg = BindingRegistry(str(CHAT))
    reconcile_bindings(reg, live, agents, _StubTopicApi(),
                       dispatched={wid}, gate_for=lambda _w: None, now_epoch=now)
    assert reg.thread_for_window(wid) is None, \
        "a dispatched, unblocked window must be withheld (lazy-bind)"

    # 3. Dispatched and blocked on a human — lazy-bind fires. Bound.
    reg = BindingRegistry(str(CHAT))
    reconcile_bindings(reg, live, agents, _StubTopicApi(),
                       dispatched={wid}, gate_for=lambda _w: "blocked-on-a-human",
                       now_epoch=now)
    assert reg.thread_for_window(wid) is not None, \
        "a dispatched window that blocks on a human must be bound"


# --------------------------------------------------------------------------
# Minimal PTB update/context fakes (only what _on_new touches).
# --------------------------------------------------------------------------

class _Msg:
    def __init__(self, text, thread_id=None):
        self.text = text
        self.message_thread_id = thread_id
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text, reply_markup=None, **_kw):
        self.replies.append((text, reply_markup))


class _CmdUpdate:
    def __init__(self, text, chat_id=CHAT, thread_id=None):
        self.message = _Msg(text, thread_id)
        self.effective_chat = type("Chat", (), {"id": chat_id})()
        self.callback_query = None


class _Ctx:
    def __init__(self):
        self.user_data: dict = {}
