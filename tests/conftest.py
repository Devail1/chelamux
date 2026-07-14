"""Suite-wide isolation from the developer's real chela install.

``chela/config.py`` sources ``$CHELA_DIR/chela.env`` at import — that is the point of the
env file: one place, read by every process. A test *is* one of those processes, so on a
machine that actually runs chela the suite would silently inherit that machine's session
name, dashboard port and terminal flags, and start passing (or failing) for reasons that
have nothing to do with the code. This repo has already shipped three bugs that were green
in CI and broken live; a suite that reads live state is how that keeps happening.

``CHELA_ENV_FILE=""`` turns the file off. It is set here, at conftest import — before any
test module imports ``chela.config`` — because the load happens at import time and no
fixture runs early enough to prevent it.

The same rule now covers Claude Code's own config directory: ``chela doctor`` reads the
INSTALLED plugin (``~/.claude/plugins/…`` — the manifest agents actually load), so on a
developer's machine the suite would read *that* machine's install. It already did: a
doctor test passed only because the real cache happened to agree with what the test
rendered. ``CLAUDE_CONFIG_DIR`` points every test at an empty directory of its own; a test
that wants an installed plugin puts one there itself.

**``CHELA_DIR`` itself was the hole, and it was the worst one.** Everything above kept the
suite from *reading* the developer's install; nothing kept it from *writing* it.
``event_log.log_path()`` resolves to ``$CHELA_DIR/events.jsonl``, so every test that
appended an event appended it to the developer's REAL log — 43 synthetic
``hook.permission_request`` rows (``session_id="s1"``, a ``-repo`` transcript slug) were
found sitting in production on 2026-07-14. A suite that reads live state renders a green
run meaningless; a suite that *writes* live state corrupts the product. Same class of bug,
one turn worse.

So ``CHELA_DIR`` is redirected here, at conftest import, for the same reason
``CHELA_ENV_FILE`` is: ``chela.config`` reads it at *import*, and several modules latch a
path derived from it at import too (``dispatcher.DB_PATH``, ``scheduler.DB_PATH``,
``launcher._STORE``…) — a ``monkeypatch.setenv`` in a fixture runs far too late for any of
them. :data:`SANDBOX_CHELA_DIR` is what those import-time latches see; the autouse
:func:`_isolate_chela_dir` then hands every individual test a scratch dir of its *own*, so
one test's ``events.jsonl`` is not the next one's. A test that wants a state file
(``events.jsonl``, ``inbox.json``, ``dispatch-hold.json``, ``daemon.json``,
``dashboard.port``, ``gates/``) puts one there itself.

**And the fence, because a comment is only a wish.** :func:`_no_live_state` wraps ``open``
for the duration of every test and *fails the run* if anything — test or product code —
opens a path under the real ``~/.chela`` or ``~/.claude``. It raises
:class:`LiveStateEscape`, which derives from ``BaseException`` **on purpose**:
``event_log.append`` swallows ``Exception`` so a crashing hook can never wedge a live
agent, and a guard a writer can catch is not a guard. Seven instances of this bug class
landed in a single day; that is a missing mechanism, not seven mistakes, and this is the
mechanism. Agent **worktrees** live under ``~/.chela/worktrees`` (this checkout may be one
of them), so that subtree is source code, not live state, and is exempt.
"""
import atexit
import builtins
import io
import os
import shutil
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("CHELA_ENV_FILE", "")

# The live state this suite must never touch — captured BEFORE the redirect below, so an
# exported CHELA_DIR is guarded too, and ~/.chela always is (it is the production default,
# and the default is what a forgetful `unset` falls back to).
REAL_CHELA_DIRS: tuple[Path, ...] = tuple({
    Path(p).expanduser().resolve()
    for p in (os.environ.get("CHELA_DIR"), Path.home() / ".chela")
    if p
})
# Claude Code's own config dir, for the same reason (CLAUDE_CONFIG_DIR is isolated per
# test, but code that hardcodes ~/.claude — transcripts.py did — bypasses that).
REAL_CLAUDE_DIR: Path = Path.home() / ".claude"

# Dispatched agents get a git worktree under ~/.chela/worktrees — one of them may BE the
# checkout the suite is running from. That subtree is source, not state; reading it is
# not the bug.
_EXEMPT: tuple[Path, ...] = tuple(d / "worktrees" for d in REAL_CHELA_DIRS)

# What every import-time latch (dispatcher.DB_PATH, scheduler.DB_PATH, launcher._STORE,
# config.CONTEXT_CACHE_DIR …) resolves against. Per-test dirs come from the fixture below;
# this one exists because those latches happen before any fixture can run.
SANDBOX_CHELA_DIR = Path(tempfile.mkdtemp(prefix="chela-tests-"))
os.environ["CHELA_DIR"] = str(SANDBOX_CHELA_DIR)
atexit.register(shutil.rmtree, SANDBOX_CHELA_DIR, ignore_errors=True)

# A developer who exported these to debug against live files would otherwise aim the whole
# suite straight back at production, under the sandbox's nose.
for _var in ("CHELA_EVENTS_FILE", "CHELA_INBOX_FILE"):
    os.environ.pop(_var, None)

# tmux is live state too, and it is not a file, so the fence below cannot catch it.
# ``chela doctor`` now READS BACK from tmux (does the session exist? is the window a run
# claims still alive? — the runtime-truth registry), so on this machine the suite would be
# asking the developer's REAL fleet, and its answers would change with whatever happens to
# be running. Every test gets a session name nothing can have; a test that wants a window
# table hands the code one (see tests/test_runtime_truth.py).
os.environ["CHELA_TMUX_SESSION"] = "chela-tests-no-such-session"


class LiveStateEscape(BaseException):
    """A test reached the developer's real ``~/.chela`` / ``~/.claude``.

    ``BaseException``, not ``Exception``: the writers this guards (``event_log.append``,
    ``inbox``) deliberately swallow ``Exception`` so a failing hook cannot stall a live
    agent — a guard they can catch is no guard at all.
    """


def _is_live_state(path) -> bool:
    try:
        p = Path(os.fspath(path))
    except TypeError:
        return False                      # an fd, not a path — not ours to judge
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        p = Path(os.path.normpath(p))
    except OSError:                       # pragma: no cover - defensive
        return False
    roots = (*REAL_CHELA_DIRS, REAL_CLAUDE_DIR)
    if not any(p == r or r in p.parents for r in roots):
        return False
    return not any(p == e or e in p.parents for e in _EXEMPT)


@pytest.fixture(autouse=True)
def _isolate_claude_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))


@pytest.fixture(autouse=True)
def _isolate_chela_dir(tmp_path, monkeypatch):
    """A scratch ``$CHELA_DIR`` per test — env AND ``config.CHELA_DIR``.

    The env var is what a subprocess (and any module resolving it lazily) sees; the
    attribute is what the modules that read ``config.CHELA_DIR`` per call see. Both, or
    half the suite lands in the session-wide sandbox above and tests start sharing an
    ``events.jsonl``.
    """
    from chela import config

    # ``.chela`` (dotted, as in production) — several tests make their OWN ``tmp_path /
    # "chela"`` with ``mkdir(exist_ok=False)``, and this fixture must not collide with one.
    # NOT created here: the product code creates its state dir on first write (that is the
    # real code path), and a test asserting its ``tmp_path`` is untouched must stay right.
    scratch = tmp_path / ".chela"
    monkeypatch.setenv("CHELA_DIR", str(scratch))
    monkeypatch.setattr(config, "CHELA_DIR", scratch)


@pytest.fixture(autouse=True)
def _no_live_state(monkeypatch):
    """The fence: opening anything under the real ``~/.chela`` / ``~/.claude`` fails.

    Wraps the one call every Python file read/write funnels through, so it catches the
    product code a test drives, not just the test's own lines. It fires on reads as well
    as writes — CMX-33/46/56 were all *reads* of live state, and a test that reads the
    machine it runs on is only green by luck.
    """
    def guard(real):
        def guarded(file, *args, **kwargs):
            if _is_live_state(file):
                raise LiveStateEscape(
                    f"test touched LIVE chela state: {file}\n"
                    "Tests must never read or write the developer's real ~/.chela or "
                    "~/.claude. Point the code at the per-test scratch dir "
                    "(the CHELA_DIR / CLAUDE_CONFIG_DIR fixtures in tests/conftest.py), "
                    "or hand the code the path you want it to use."
                )
            return real(file, *args, **kwargs)
        return guarded

    # Three doors, not one: ``Path.open`` (and so ``read_text``/``write_text``) goes
    # through ``io.open``, which is a *separate reference* to the same function that
    # ``builtins.open`` names — patching one leaves the other wide open. ``os.open`` is
    # the low-level door (``os.mkdir``/sqlite bypass all three, but the state files that
    # escaped are JSON and JSONL).
    monkeypatch.setattr(builtins, "open", guard(builtins.open))
    monkeypatch.setattr(io, "open", guard(io.open))
    monkeypatch.setattr(os, "open", guard(os.open))
