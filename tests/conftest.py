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
import subprocess
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

# **pm2 is live state too, and it is the one the fence below (`_no_live_pm2_restart`)
# cannot fully close.** Issue #466: nothing anywhere in this repo sets `PM2_HOME`, so every
# `pm2` subprocess — including from routes that fence never sees, because it is an
# in-process `monkeypatch.setattr(chela.update, "_sh", ...)` — defaults to the OPERATOR'S
# REAL God Daemon at `~/.pm2`. `tests/test_graceful_shutdown.py` spawns the actual `python
# -m chela.main run` daemon as a real subprocess; the dashboard's `/api/update/apply` route
# runs `update.apply()` on a background thread that can outlive the test function whose
# monkeypatch already unwound. Either one, unmocked, reaches real `pm2 restart` on this
# box. Measured 2026-09-09: 315+ real `Stopping app:` commands against all four `chela-*`
# services in one test/judge window — no OOM, no traceback, pure disruption, and a live
# session went hook-blind because its dashboard kept bouncing mid-request.
#
# `PM2_HOME=$(mktemp -d)` PER TEST would isolate it but leaks a fresh pm2 God Daemon —
# pm2 spawns one, in whatever home it's pointed at, on first use — every time any test
# touches pm2. So this is ONE throwaway home for the whole session, same shape as
# `SANDBOX_CHELA_DIR` above, with its own `atexit` teardown that kills whatever daemon may
# have started there (`pm2 kill` against THIS home, never the operator's) before removing
# the directory: no God Daemon survives the run.
#
# A `pm2 jlist` read against this empty home answers `[]`, not an error, and that is
# deliberate: `chela.update._online_chela_services` already treats an empty list as the
# normal "nothing to restart" case (a dev checkout run by hand has no services either),
# which is exactly what the ~20 `chela doctor` / `runtime_truth` tests that exercise
# `repo.services_current` unstubbed rely on — they assert the READ doesn't crash, never
# the operator's actual fleet contents (which no test can know ahead of time). Making pm2
# UNREACHABLE instead (e.g. blocking its socket) would fail those reads outright, trading
# this bug for exactly the one CMX-346's own fence comment warns against.
SANDBOX_PM2_HOME = Path(tempfile.mkdtemp(prefix="chela-tests-pm2-"))
os.environ["PM2_HOME"] = str(SANDBOX_PM2_HOME)


def _kill_sandbox_pm2_daemon() -> None:
    if shutil.which("pm2"):
        try:
            subprocess.run(
                ["pm2", "kill"],
                env={**os.environ, "PM2_HOME": str(SANDBOX_PM2_HOME)},
                capture_output=True, timeout=15,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass  # nothing to kill, or pm2 hung on its own way out — the rmtree below
                  # still removes the pid/socket files so nothing points at this home again
    shutil.rmtree(SANDBOX_PM2_HOME, ignore_errors=True)


atexit.register(_kill_sandbox_pm2_daemon)

# A developer who exported these to debug against live files would otherwise aim the whole
# suite straight back at production, under the sandbox's nose.
for _var in ("CHELA_EVENTS_FILE", "CHELA_INBOX_FILE"):
    os.environ.pop(_var, None)

# **And the suite must not be able to DISPATCH.** This one is not about reading or writing
# a file: `CHELA_DISPATCH_WORKFLOWS` is the daemon's work queue, and the developer's shell
# has it set (that is how chela runs on this box). tests/test_graceful_shutdown.py spawns
# the REAL `python -m chela.main run` daemon and passes `os.environ` through, so on
# 2026-07-14 `pytest` loaded the real WORKFLOW.md and the real TODO.md and dispatched REAL
# OPEN TASKS into ~/.chela/worktrees — it only failed because it collided with a live run's
# worktree. On a clean box it would have spawned agents. Blanked here, for every test and
# every subprocess a test starts: nothing under pytest may claim work.
os.environ["CHELA_DISPATCH_WORKFLOWS"] = ""

# tmux is live state too, and it is not a file, so the fence below cannot catch it.
# ``chela doctor`` now READS BACK from tmux (does the session exist? is the window a run
# claims still alive? — the runtime-truth registry), so on this machine the suite would be
# asking the developer's REAL fleet, and its answers would change with whatever happens to
# be running. Every test gets a session name nothing can have; a test that wants a window
# table hands the code one (see tests/test_runtime_truth.py).
os.environ["CHELA_TMUX_SESSION"] = "chela-tests-no-such-session"

# Collapse the dispatcher's seed-delivery confirm loop to ~instant. In production it polls a
# freshly-spawned Claude window for up to 5×8s ≈ 44s, waiting for it to flip "busy" (see
# dispatcher.SEED_CONFIRM_TIMEOUT_SECONDS). No test has a real TUI to flip, so every test
# that triggers a dispatch would otherwise pay that full real-time budget doing nothing — it
# was ~80% of the suite's wall-clock (test_dispatcher_ci/_rework/_spawn_retry/_critic all sat
# at exactly 44s/88s). Set BEFORE dispatcher import so its module-level constants pick these
# up (the timeout is a default arg, bound at import — a fixture would be too late). Tests that
# exercise the retry LOGIC itself mock _agent_status/time.sleep and are unaffected.
os.environ.setdefault("CHELA_SEED_CONFIRM_TIMEOUT_SECONDS", "0.05")
os.environ.setdefault("CHELA_SEED_CONFIRM_POLL_INTERVAL", "0.01")
os.environ.setdefault("CHELA_SEED_RESEND_SETTLE_SECONDS", "0")


class LiveNotificationEscape(BaseException):
    """A test pushed a REAL notification to the operator's phone.

    ``BaseException`` for the same reason as :class:`LiveStateEscape`: ``notify.send``
    wraps its transport in ``except Exception`` so a dead notifier can never wedge a live
    daemon, and a guard the product code catches is not a guard.
    """


class LiveStateEscape(BaseException):
    """A test reached the developer's real ``~/.chela`` / ``~/.claude``.

    ``BaseException``, not ``Exception``: the writers this guards (``event_log.append``,
    ``inbox``) deliberately swallow ``Exception`` so a failing hook cannot stall a live
    agent — a guard they can catch is no guard at all.
    """


class LiveProcessEscape(BaseException):
    """A test ran ``pm2 restart`` for real via ``chela.update._sh``.

    ``update._sh`` is the sole funnel ``chela.update`` shells out through for ``pm2`` and
    ``uv`` (see :func:`chela.update._online_chela_services`, :func:`chela.update.apply`).
    A READ (``pm2 jlist``) is tolerated unstubbed elsewhere in this suite — it is how
    :func:`chela.runtime_truth`'s ``services_current`` fact gets exercised against this
    box's own real PM2 list in ordinary ``chela doctor`` tests, and it changes nothing.
    A RESTART is a different order of problem: on a machine that actually runs chela
    (this one), it hits the operator's REAL ``chela-*`` services. Not hypothetical —
    CMX-346's own new restart-on-the-up-to-date-path, exercised via ``update.apply()`` /
    ``auto_apply_sweep()`` in tests that stubbed everything else but this, read this box's
    real ``pm2 jlist``, found its real services older than a just-cloned test fixture's
    commit, and ran a real ``pm2 restart chela-daemon chela-dashboard
    chela-agent-terminals chela-telegram`` — the same shape as the 109-push notify incident
    :func:`_no_live_notifications` documents, one call site over. ``BaseException`` for the
    same reason as :class:`LiveStateEscape`: ``_sh`` never raises on its own (it returns
    ``None`` on a missing binary), so nothing product code does could ever catch this and
    mask it.
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
def _isolate_claude_projects(tmp_path, monkeypatch):
    """A scratch transcript root per test — the one hole the fence didn't cover.

    ``transcripts.CLAUDE_PROJECTS_DIR`` is a module-level constant latched at import
    from ``~/.claude/projects`` (it does not read ``CLAUDE_CONFIG_DIR``), so redirecting
    the env var above does nothing for it. Any resolver that returns None then falls back
    to ``sessions.explain`` → ``resolve_window`` → ``transcript_for_session(base=None)``,
    which globs and reads the developer's REAL transcripts — tripping
    :func:`_no_live_state` on whichever ``@N`` window happens to exist in the live tmux
    session. That is exactly why the telegram monitor/relay tests fail nondeterministically
    on a machine running chela (and why the judge, which runs on such a machine, saw the
    suite red on every dispatched run). Point it at an empty scratch dir; a test that wants
    transcripts on disk still overrides this with its own ``monkeypatch.setattr``.
    """
    from chela import transcripts

    projects = tmp_path / "claude-projects"
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", projects)


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


@pytest.fixture(autouse=True)
def _no_live_notifications(monkeypatch):
    """The fence for OUTBOUND side effects: no test may push a real notification.

    ``notify.enabled()`` is just ``bool(NOTIFY_URL)``, and ``NOTIFY_URL`` is read from the
    environment at import — so on the machine that actually runs chela, any test that
    reaches a ``notify.send`` call site pushes a REAL ntfy/Telegram message to the
    operator's phone. That is not hypothetical: on 2026-09-01 the operator received **109**
    pushes reading "auto-update applied — applied 1 commit(s), restarted no services",
    which is a test fixture's signature, not a real update (a genuine auto-update converges
    and stops; a fixture built one commit behind never does). The source was
    ``test_auto_apply_sweep_never_calls_apply_with_a_bespoke_repo_arg``, which drives the
    real ``auto_apply_sweep()`` and — alone among its three siblings — never stubbed
    ``notify``.

    CMX-115 already stripped ``CHELA_NOTIFY_URL`` from the env of every tmux-spawned agent
    and judge, which is why this stayed hidden: that covers the dispatcher's own paths, but
    NOT a maintainer running ``pytest`` or ``chela judge run`` from their own shell, which
    is the normal way this suite is run by hand.

    So it is fixed here rather than in the one test that leaked, for the same reason
    :func:`_no_live_state` exists: a comment is only a wish, and a suite where any of ~3500
    tests can page a human is one refactor away from doing it again. Both doors are shut —
    ``NOTIFY_URL`` is blanked so ``enabled()`` gates the call sites off, and ``_post`` (the
    single funnel every transport goes through) raises if anything reaches it anyway, e.g.
    a test that sets its own URL.

    A test that genuinely wants to exercise the transport overrides ``_post`` itself; its
    ``monkeypatch.setattr`` runs after this one and wins.
    """
    from chela import notify

    def guarded(req):
        raise LiveNotificationEscape(
            "test sent a REAL notification: "
            f"{getattr(req, 'full_url', req)!r}\n"
            "Tests must never push to the operator's notifier. Stub the module the code "
            "under test calls (`monkeypatch.setattr(update, 'notify', stub)`), or patch "
            "`notify._post` if the transport itself is what you are testing."
        )

    monkeypatch.setattr(notify, "NOTIFY_URL", "")
    monkeypatch.setattr(notify, "_post", guarded)


@pytest.fixture(autouse=True)
def _no_live_pm2_restart(monkeypatch):
    """The fence for the ONE mutating call in ``chela.update._sh``: no test may run a REAL
    ``pm2 restart``. See :class:`LiveProcessEscape` for the incident this closes.

    Narrower than :func:`_no_live_state` / :func:`_no_live_notifications` on purpose:
    ``pm2 jlist`` (a read) is left to run for real when a test doesn't stub ``_sh`` at all,
    because that is already how ordinary ``chela doctor`` / ``runtime_truth`` tests on this
    box exercise the ``services_current`` fact, and blocking it too would fail ~20 unrelated
    tests for a class of read this suite has always tolerated. ``pm2 restart`` is not a
    read, and a test reaching it for real gets this fence instead of a real restart.

    A test that wants ``update.apply()`` (or anything calling through it —
    ``services_running_stale_code``, ``auto_apply_sweep``) to actually restart something
    overrides this with its own ``monkeypatch.setattr(update, "_sh", fake_sh)``, same as
    today; that call happens inside the test body, after this fixture, so it wins.

    Deliberately NOT the only thing standing between a test and the operator's real fleet
    (issue #466): this is in-process, so it never sees a subprocess a test spawns, or a
    background thread that outlives the test whose monkeypatch installed it. ``PM2_HOME``
    above is the structural layer that holds even then — this one stays for the fast,
    legible failure it gives a test that reaches ``pm2 restart`` synchronously through
    ``_sh``, same process, same call stack.
    """
    from chela import update

    real_sh = update._sh

    def guarded(args, cwd, timeout=update._SHELL_TIMEOUT_SECONDS):
        if args[:2] == ["pm2", "restart"]:
            raise LiveProcessEscape(
                f"test ran a REAL `pm2 restart` via chela.update._sh: {args!r} "
                f"(cwd={cwd!r})\nTests must never restart the operator's real pm2 "
                "services. Stub `chela.update._sh` (see tests/test_update.py's "
                "`_FakeCP` / `_pm2_stub`)."
            )
        return real_sh(args, cwd, timeout=timeout)

    monkeypatch.setattr(update, "_sh", guarded)


# tmux is live state too, and unlike pm2 there is no single funnel to patch: at least six
# modules (`chela/restore.py`, `chela/spawn.py`, `chela/dispatcher.py`,
# `chela/agent_manager.py`, `chela/messenger.py`, `chela/discovery.py`) shell out to it
# directly. Issue #494: CMX-361/#492 closed ONE instance of this — `chela.restore
# ._default_kill_window` reached the operator's real default tmux socket via
# `tests/test_restore.py`'s unstubbed `resume()` — by hand, giving `_resume_kit()` an
# explicit no-op `kill_window`. The identical gap survived one file over:
# `tests/test_restore_cli.py`'s OWN `live_stores` fixture fakes every other leaf but not
# this one, so `test_CHELA_RESTORE_RESUME_true_env_var_alone_actually_enables_the_launch`
# and `test_chela_restore_resume_refuses_a_row_whose_task_is_still_ACTIVE` issue a REAL
# `tmux kill-window -t @99` on the operator's default socket (measured 2026-09: 3 calls in
# a 3950-test run). `@99` is a plausible LIVE window id — tmux reissues low ids freely
# after churn — so on a host where it exists, running this suite kills a live agent's
# window. Fixing instance-by-instance is what let it recur once already; this closes the
# SEAM instead of the leaf, so a future one can't reopen it the same way.
#
# A `tmux` invocation is provably harmless even on the default socket in exactly three
# shapes: it is READ-ONLY (`display-message`, `list-windows`, `has-session`,
# `show-environment`, `capture-pane`, …— nothing changes); it MUTATES but targets
# `CHELA_TMUX_SESSION` (set above, at conftest import, before any module that latches
# `TMUX_SESSION`/`config.current_session()` at import or call time can see anything else)
# — a session name built so it can never exist, so tmux errors out on it rather than
# touching a real window; or `argv[0]` does not actually resolve (via the PATH the call
# will run with) to the real system `tmux` binary at all — several tests (e.g.
# `tests/test_terminals_selfheal.py`) put a PATH-shim script named `tmux` ahead of the
# real one that transparently injects its own `-L <scratch socket>`, so the argv this
# fence sees never carries `-L` even though the exec that actually runs never touches the
# default socket either. Only a call that genuinely reaches the real binary is judged on
# its argv. Every mutating call this codebase itself makes is scoped one of these ways
# EXCEPT `_default_kill_window`, which — by design (see its own docstring: window ids are
# unique server-wide) — targets a bare `@N` with no session qualifier at all. That is
# exactly the shape this fence refuses.
_TMUX_MUTATING_SUBCOMMANDS = frozenset({
    "kill-window", "kill-session",
    "new-window", "new-session",
    "send-keys", "rename-window",
    "respawn-window", "respawn-pane",
})

# Captured at import, before any test can shadow `tmux` on `$PATH` — the fixed point
# every PATH-shim comparison below is measured against.
_REAL_TMUX_BIN = shutil.which("tmux")
_REAL_TMUX_REALPATH = os.path.realpath(_REAL_TMUX_BIN) if _REAL_TMUX_BIN else None


class LiveTmuxMutationEscape(BaseException):
    """A test issued a MUTATING tmux subcommand against the operator's default socket.

    See the comment above :data:`_TMUX_MUTATING_SUBCOMMANDS` (issue #494) for the incident
    this closes. ``BaseException``, not ``Exception``, for the same reason as
    :class:`LiveStateEscape`: several of the call sites this fence watches
    (``chela.spawn._send``, ``chela.agent_manager.reconcile_window_names``) deliberately
    swallow ``Exception`` around the tmux call so a hiccup can never wedge a live agent — a
    guard they can catch is no guard at all.
    """


def _resolves_to_real_tmux(prog: str, env: dict | None) -> bool:
    """Whether `prog`, resolved against the PATH the call will actually run with, IS the
    real system tmux binary captured at import — as opposed to a test's own PATH-shim
    script that merely happens to also be named ``tmux``."""
    if _REAL_TMUX_REALPATH is None:
        return False
    path = (env if env is not None else os.environ).get("PATH")
    resolved = shutil.which(prog, path=path)
    if resolved is None:
        return False
    return os.path.realpath(resolved) == _REAL_TMUX_REALPATH


def _tmux_violation(argv: list, env: dict | None) -> str | None:
    """``None`` if `argv` is a safe tmux invocation (or isn't tmux at all); else why not."""
    if not argv:
        return None
    try:
        prog = str(os.fspath(argv[0]))
    except TypeError:
        return None  # not a path-like argv[0] (e.g. a shell string) — not ours to judge
    if os.path.basename(prog) != "tmux" or not _resolves_to_real_tmux(prog, env):
        return None
    rest = [str(a) for a in argv[1:]]
    if "-L" in rest or "-S" in rest:
        return None  # an explicit/private socket — never the operator's default
    if not rest or rest[0] not in _TMUX_MUTATING_SUBCOMMANDS:
        return None  # read-only, or a subcommand this fence doesn't police
    subcmd = rest[0]
    safe_session = os.environ.get("CHELA_TMUX_SESSION", "")
    if safe_session:
        target = None
        for flag in ("-t", "-s"):  # -t targets an existing window/session, -s names a new one
            try:
                target = rest[rest.index(flag) + 1]
                break
            except (ValueError, IndexError):
                continue
        if target is not None and (target == safe_session
                                    or target.startswith(f"{safe_session}:")):
            return None  # scoped to a session that can never exist
    return (
        f"tmux {subcmd!r} on the DEFAULT socket: argv={argv!r}. A mutating tmux "
        "subcommand must never reach the operator's real server. Target a private "
        "`-L`/`-S` socket (see tests/test_epoch_live.py's TMUX_BIN helper), or stub the "
        "seam that issues it (e.g. `monkeypatch.setattr(restore_mod, "
        "'_default_kill_window', lambda wid: None)`)."
    )


@pytest.fixture(autouse=True)
def _no_live_tmux_mutation(monkeypatch):
    """The fence: any MUTATING tmux subcommand reaching the default socket fails the test.

    Patches ``subprocess.Popen.__init__`` — the one primitive ``subprocess.run``/``call``/
    ``check_call``/``check_output`` all construct under the hood — rather than any single
    chela module, because tmux has no funnel equivalent to ``update._sh``. A test that
    wants the real thing overrides the SEAM (the function that calls tmux), not this fence,
    same as :func:`_no_live_pm2_restart` documents for pm2.
    """
    real_init = subprocess.Popen.__init__

    def guarded_init(self, args, *a, **kw):
        if isinstance(args, (list, tuple)):
            reason = _tmux_violation(list(args), kw.get("env"))
            if reason is not None:
                raise LiveTmuxMutationEscape(f"test issued a {reason}")
        return real_init(self, args, *a, **kw)

    monkeypatch.setattr(subprocess.Popen, "__init__", guarded_init)
