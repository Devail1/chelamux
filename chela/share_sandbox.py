"""🔐🧱 Sandboxed share sessions — the ONLY kind of window a share guest may type into.

A share hands a guest the live terminal (``chela/collab_stream.py``). Letting them TYPE
into an ordinary window would hand them a real shell as the operator, so
``collab_stream`` forwards guest keystrokes only when :func:`is_sandboxed_share_session`
verifies — from the LIVE process tree and container state, never from a flag chela
stored — that the window is one of these.

**Why a container, not Claude Code's native sandbox** (measured by hand in CMX-400;
results table in ``docs/SHARE_SANDBOX.md``). The native
sandbox (``--settings`` with ``sandbox.enabled``, the #502 route in
:mod:`chela.sandbox`) confines the *Bash tool* well — but ``!`` shell-mode commands run
**unsandboxed** in the Claude process, and nothing turns shell mode off: not
``--restricted``, not ``--tools`` without Bash, not a ``Bash`` deny rule. A guest who types
``!cat ~/.ssh/id_ed25519`` reads it. So the boundary has to sit *around* the Claude process:

* the guest's Claude runs in a container that sees **only the workspace** (read-write, with
  ``.env*`` files masked and existing ``.git``/``.claude``/… mounted read-only) plus a
  read-only Claude binary — no ``~/.ssh``, ``~/.claude``, ``~/.chela``, ``~/.config``;
* the container runs as the host uid, ``--cap-drop ALL``, ``no-new-privileges``, read-only
  root, tmpfs ``/tmp`` + ``HOME``, and memory / pid caps;
* its only network is a per-session ``--internal`` bridge with **no host-side IP**
  (``inhibit_ipv4``) — so it reaches no host service and no internet — whose one other
  member is a sidecar running :mod:`chela.share_proxy`, which adds the operator's token on
  the way out. **The raw token never enters the guest container**;
* the window's process is this module's launcher, started by tmux directly (no shell), so
  exiting Claude ends the container, the launcher, and the pane.

It fails closed: :func:`preflight` refuses to start without docker, the image, the Claude
binary or the token file, and refuses a workspace that would expose secrets (``$HOME``
itself, an ancestor of it, or anything under a secrets directory).

**Network modes** (CMX-418), chosen per session at launch and carried in the launcher's own
argv (``--net``), so the live check reads it from the process tree, not from a flag:

* ``none`` (the default) — the above: the token proxy is the only other member of the
  network, and the guest has no proxy env.
* ``web`` (opt-in) — the guest is STILL alone on its ``--internal``, no-host-address
  network with the token proxy, plus ONE more sidecar: :mod:`chela.share_web_proxy`, a
  filtering HTTP(S) forward proxy that resolves names itself, refuses any non-public
  address after resolution, allows ports 80/443 only, rate-limits per site and logs every
  request to a per-session file. The guest runs a browser image (headless Chromium) and
  gets ``HTTPS_PROXY``/``HTTP_PROXY`` pointing at it. :func:`check_share_session` verifies
  the mode the pane was launched with against the live container, its env and the
  network's member set — a ``none`` session that gained a web sidecar or proxy env fails.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from chela import config
from chela.transcripts import claude_config_dir

log = logging.getLogger(__name__)

LAUNCH_MODULE = "chela.share_sandbox"
LABEL = "dev.chela.share-sandbox"
CONTAINER_PREFIX = "chela-share-"
PROXY_PREFIX = "chela-share-proxy-"
NETWORK_PREFIX = "chela-share-net-"
GUEST_WORKDIR = "/workspace"
GUEST_HOME = "/home/guest"
CLAUDE_MOUNT = "/usr/local/bin/claude"
PROXY_ALIAS = "chela-proxy"
PROXY_PORT = 8080
PROXY_SCRIPT_MOUNT = "/run/chela/share_proxy.py"
PROXY_TOKEN_MOUNT = "/run/chela/token"
PROXY_SESSION_MOUNT = "/run/chela/session"
DEFAULT_IMAGE = "python:3.12-slim"
GUEST_MEMORY = "2g"
GUEST_PIDS = "512"
PROXY_MEMORY = "256m"

NET_NONE, NET_WEB = "none", "web"
NET_MODES = (NET_NONE, NET_WEB)
NET_LABEL = "dev.chela.share-sandbox.net"
WEB_PREFIX = "chela-share-web-"
WEB_ALIAS = "chela-web"
WEB_PORT = 3128
WEB_URL = f"http://{WEB_ALIAS}:{WEB_PORT}"
WEB_SCRIPT_MOUNT = "/run/chela/share_web_proxy.py"
WEB_LOG_MOUNT = "/run/chela/web.log"
DEFAULT_WEB_IMAGE = "chela-share-web:latest"
WEB_IMAGE_DIR = Path(__file__).with_name("assets") / "share-sandbox-web"
# Chromium's process + thread count on top of Claude's — pids-limit counts threads.
GUEST_PIDS_WEB = "1024"
# The guest env a web session sets; a `none` session must carry NONE of these keys.
PROXY_ENV_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy",
                  "all_proxy")
_NO_PROXY = PROXY_ALIAS

_SID_RE = re.compile(r"^[0-9a-f]{12}$")

# Workspace entries a guest could plant for the OPERATOR's next unsandboxed tool to run
# (git hooks / core.fsmonitor, project Claude hooks + MCP servers, direnv, editor tasks):
# mounted read-only when they exist. ``.env*`` files are masked with /dev/null outright.
READONLY_ENTRIES = (".git", ".claude", ".mcp.json", ".envrc", ".vscode")
_ENV_FILE_RE = re.compile(r"^\.env(\..+)?$")
_MASK_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox"}
_MASK_MAX_DEPTH = 4

# Directories under $HOME that hold secrets or other sessions' state; a workspace may not
# be one of them, inside one, or contain one.
SECRET_DIRS = (".ssh", ".claude", ".chela", ".config", ".aws", ".gnupg", ".docker",
               ".kube", ".netrc", ".git-credentials", ".secrets", ".local")


def image() -> str:
    return os.environ.get("CHELA_SHARE_SANDBOX_IMAGE", "").strip() or DEFAULT_IMAGE


def token_file() -> Path:
    """The file the proxy reads the operator's token from: ``$CHELA_SHARE_SANDBOX_TOKEN_FILE``
    (e.g. a bare ``claude setup-token`` token), else Claude Code's own
    ``<config dir>/.credentials.json`` (Linux/WSL; macOS keeps it in the keychain)."""
    raw = os.environ.get("CHELA_SHARE_SANDBOX_TOKEN_FILE", "").strip()
    return Path(raw).expanduser() if raw else claude_config_dir() / ".credentials.json"


def session_root() -> Path:
    """Host-side per-session state written by the proxy sidecar (CMX-420): one directory
    per session id, bind-mounted read-write into THAT session's proxy only — never into
    the guest (:func:`guest_run_argv` has no such mount, and :func:`verify_container`
    refuses any mount outside the workspace)."""
    return config.CHELA_DIR / "share-sessions"


def session_dir(sid: str) -> Path:
    return session_root() / sid


def outbox_path(sid: str) -> Path:
    from chela.share_proxy import OUTBOX_NAME
    return session_dir(sid) / OUTBOX_NAME


def proxy_status_path(sid: str) -> Path:
    from chela.share_proxy import STATUS_NAME
    return session_dir(sid) / STATUS_NAME


def activity_path(sid: str) -> Path:
    from chela.share_proxy import ACTIVITY_NAME
    return session_dir(sid) / ACTIVITY_NAME


def proxy_activity_status(sid: str, now: float | None = None) -> str | None:
    """``busy``/``idle`` as the session's credential proxy saw it (CMX-436), or None when
    there is no readable activity file. Never ``waiting`` — a prompt is not a request."""
    from chela.share_proxy import activity_status
    try:
        state = json.loads(activity_path(sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return activity_status(state, now)


# Session dirs whose outbox has not been written for this long are removed at the next
# launch — they hold everything a guest's session said, and nothing reads them once the
# window is gone.
SESSION_DIR_MAX_AGE_S = 7 * 86400


def prune_session_dirs(now: float | None = None) -> None:
    now = time.time() if now is None else now
    root = session_root()
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for d in entries:
        if not (_SID_RE.match(d.name) and d.is_dir() and not d.is_symlink()):
            continue
        try:
            newest = max([d.stat().st_mtime] + [f.stat().st_mtime for f in d.iterdir()])
        except OSError:
            continue
        if now - newest > SESSION_DIR_MAX_AGE_S:
            shutil.rmtree(d, ignore_errors=True)


# --- the per-session Telegram relay opt-out (CMX-420) ----------------------------------
#
# Default ON (Liav, 2026-10-01): a sandboxed session relays to its topic like any agent.
# Keyed by SESSION id, not window id — ``@N`` is reissued after a tmux restart, and an
# opt-out must not silently attach itself to whatever window inherits the number.

def _relay_optout_path() -> Path:
    return config.CHELA_DIR / "share-relay-optout.json"


def _relay_optouts() -> set[str]:
    try:
        data = json.loads(_relay_optout_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {s for s in data if isinstance(s, str)} if isinstance(data, list) else set()


def relay_enabled(sid: str) -> bool:
    """Whether a sandboxed session's replies are relayed to its Telegram topic."""
    return sid not in _relay_optouts()


def set_relay_enabled(sid: str, on: bool) -> None:
    if not _SID_RE.match(sid):
        raise ValueError(f"not a sandboxed-session id: {sid!r}")
    off = _relay_optouts()
    (off.discard if on else off.add)(sid)
    path = _relay_optout_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(off)), encoding="utf-8")
    os.replace(tmp, path)


def proxy_upstream() -> str:
    return os.environ.get("CHELA_SHARE_PROXY_UPSTREAM", "").strip() or "https://api.anthropic.com"


def web_image() -> str:
    return os.environ.get("CHELA_SHARE_SANDBOX_WEB_IMAGE", "").strip() or DEFAULT_WEB_IMAGE


def guest_image(net: str) -> str:
    return web_image() if net == NET_WEB else image()


def web_log_path(sid: str) -> Path:
    """The per-session request log the web proxy appends to (operator-readable)."""
    return Path(config.CHELA_DIR) / "share-web" / f"{sid}.log"


def claude_binary() -> str | None:
    found = shutil.which("claude")
    return os.path.realpath(found) if found else None


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def container_name(sid: str) -> str:
    return CONTAINER_PREFIX + sid


def proxy_name(sid: str) -> str:
    return PROXY_PREFIX + sid


def web_proxy_name(sid: str) -> str:
    return WEB_PREFIX + sid


def network_name(sid: str) -> str:
    return NETWORK_PREFIX + sid


def launcher_argv(sid: str, cwd: str, net: str = NET_NONE) -> list[str]:
    """The window's exact command. tmux runs a multi-argument command directly (no shell),
    so this process IS the pane — :func:`verify_pane` checks for exactly this shape, and
    reads the network mode back out of it."""
    if net not in NET_MODES:
        raise ValueError(f"unknown network mode {net!r}")
    return [sys.executable, "-m", LAUNCH_MODULE, "run", "--id", sid, "--net", net, cwd]


# --- workspace policy ------------------------------------------------------------------

def workspace_refusal(cwd: str) -> str | None:
    """Why ``cwd`` may not be a sandboxed workspace, or None. The workspace is the one
    host path the guest can read and write, so it must not be (or contain) the operator's
    secrets."""
    real = os.path.realpath(os.path.expanduser(cwd))
    if not os.path.isdir(real):
        return f"no such directory: {cwd}"
    home = os.path.realpath(os.path.expanduser("~"))
    if real == os.sep:
        return "the filesystem root cannot be a sandboxed workspace"
    if real == home or home.startswith(real.rstrip(os.sep) + os.sep):
        return "your home directory (or a parent of it) cannot be a sandboxed workspace — pick a project directory"
    protected = [os.path.join(home, d) for d in SECRET_DIRS]
    protected += [os.path.realpath(str(config.CHELA_DIR)), os.path.realpath(str(claude_config_dir()))]
    for p in protected:
        if real == p or real.startswith(p.rstrip(os.sep) + os.sep):
            return f"{cwd} is inside {p}, which holds secrets or session state"
    # The proxy's outbox holds every session's replies; a workspace that CONTAINS it (a
    # CHELA_DIR relocated into a project) would hand the guest all of them.
    outboxes = os.path.realpath(str(session_root()))
    if outboxes.startswith(real.rstrip(os.sep) + os.sep):
        return f"{cwd} contains {outboxes}, which holds sandboxed sessions' replies"
    return None


def env_masks(cwd: str) -> list[str]:
    """``.env`` / ``.env.*`` files under ``cwd`` (bounded depth, skipping vendored trees),
    relative to it — each is masked with ``/dev/null`` in the guest's view."""
    out: list[str] = []
    root = os.path.realpath(cwd)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        dirnames[:] = [d for d in dirnames if d not in _MASK_SKIP_DIRS and depth < _MASK_MAX_DEPTH]
        for f in filenames:
            if _ENV_FILE_RE.match(f) and not os.path.islink(os.path.join(dirpath, f)):
                out.append(os.path.normpath(os.path.join(rel, f)))
    return sorted(out)


def readonly_entries(cwd: str) -> list[str]:
    root = os.path.realpath(cwd)
    return [e for e in READONLY_ENTRIES
            if os.path.lexists(os.path.join(root, e)) and not os.path.islink(os.path.join(root, e))]


# --- the docker command lines ------------------------------------------------------------

def network_create_argv(sid: str) -> list[str]:
    # --internal: no route out. inhibit_ipv4: the HOST gets no address on this bridge, so
    # nothing listening on the host (0.0.0.0 services included) is reachable from it.
    return ["docker", "network", "create", "--internal",
            "-o", "com.docker.network.bridge.inhibit_ipv4=true",
            "--label", f"{LABEL}={sid}", network_name(sid)]


def proxy_run_argv(sid: str, uid: int, gid: int) -> list[str]:
    proxy_src = str(Path(__file__).with_name("share_proxy.py"))
    return ["docker", "run", "-d", "--rm", "--name", proxy_name(sid),
            "--label", f"{LABEL}={sid}",
            "--user", f"{uid}:{gid}", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--read-only",
            "--memory", PROXY_MEMORY, "--pids-limit", "64",
            "-v", f"{proxy_src}:{PROXY_SCRIPT_MOUNT}:ro",
            "-v", f"{token_file()}:{PROXY_TOKEN_MOUNT}:ro",
            # Read-write, and on the PROXY only: where it writes the Telegram outbox.
            "-v", f"{session_dir(sid)}:{PROXY_SESSION_MOUNT}",
            "-e", f"CHELA_PROXY_TOKEN_FILE={PROXY_TOKEN_MOUNT}",
            "-e", f"CHELA_PROXY_SESSION_DIR={PROXY_SESSION_MOUNT}",
            "-e", f"CHELA_PROXY_UPSTREAM={proxy_upstream()}",
            "-e", f"CHELA_PROXY_PORT={PROXY_PORT}",
            image(), "python", PROXY_SCRIPT_MOUNT]


def host_deny_nets() -> list[str]:
    """This host's own interface addresses (best effort), handed to the web proxy as extra
    denied destinations — on a host with a PUBLIC address, that address is "global" yet
    still the host, and its 0.0.0.0 services must stay out of reach."""
    out: set[str] = set()
    try:
        p = subprocess.run(["ip", "-o", "addr", "show"], capture_output=True, text=True, timeout=5)
        for line in p.stdout.splitlines():
            m = re.search(r"\binet6? ([0-9a-fA-F:.]+)/", line)
            if m:
                out.add(m.group(1))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return sorted(out)


def web_proxy_run_argv(sid: str, uid: int, gid: int) -> list[str]:
    """The web egress sidecar. Unlike the guest it sits on docker's default bridge (its
    route out), so every destination check lives in :mod:`chela.share_web_proxy`; it holds
    no credential, mounts only its own script (ro) and its log file (rw)."""
    src = str(Path(__file__).with_name("share_web_proxy.py"))
    deny_nets = [n for n in (os.environ.get("CHELA_SHARE_WEB_DENY_CIDRS", "").split(",")
                             + host_deny_nets()) if n.strip()]
    argv = ["docker", "run", "-d", "--rm", "--name", web_proxy_name(sid),
            "--label", f"{LABEL}={sid}", "--label", f"{NET_LABEL}={NET_WEB}",
            "--user", f"{uid}:{gid}", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--read-only",
            "--memory", PROXY_MEMORY, "--pids-limit", "128",
            "-v", f"{src}:{WEB_SCRIPT_MOUNT}:ro",
            "-v", f"{web_log_path(sid)}:{WEB_LOG_MOUNT}",
            "-e", f"CHELA_WEB_LOG={WEB_LOG_MOUNT}",
            "-e", f"CHELA_WEB_PORT={WEB_PORT}",
            "-e", f"CHELA_WEB_DENY_NETS={','.join(n.strip() for n in deny_nets)}"]
    for var, val in (("CHELA_WEB_DENY", os.environ.get("CHELA_SHARE_WEB_DENY", "")),
                     ("CHELA_WEB_ALLOW", os.environ.get("CHELA_SHARE_WEB_ALLOW", "")),
                     ("CHELA_WEB_HOST_RPS", os.environ.get("CHELA_SHARE_WEB_HOST_RPS", "")),
                     ("CHELA_WEB_GLOBAL_RPS", os.environ.get("CHELA_SHARE_WEB_GLOBAL_RPS", ""))):
        val = val.strip()
        if val:
            argv += ["-e", f"{var}={val}"]
    return argv + [image(), "python", WEB_SCRIPT_MOUNT]


# Pre-seeds the guest's (tmpfs) HOME so Claude starts straight at the prompt, then
# exec's Claude so no shell remains in the container.
_GUEST_SEED = json.dumps({
    "hasCompletedOnboarding": True, "theme": "dark",
    "projects": {GUEST_WORKDIR: {"hasTrustDialogAccepted": True,
                                 "hasCompletedProjectOnboarding": True}},
})
_GUEST_ENTRY = f"printf '%s' '{_GUEST_SEED}' > {GUEST_HOME}/.claude.json && exec claude"


def guest_proxy_env(net: str) -> dict[str, str]:
    """The proxy env a guest in ``net`` mode gets — empty for ``none``."""
    if net != NET_WEB:
        return {}
    env = {k: WEB_URL for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")}
    # The token proxy is reached directly on the internal network, never via the web one.
    env.update(NO_PROXY=_NO_PROXY, no_proxy=_NO_PROXY)
    return env


def guest_run_argv(sid: str, cwd: str, uid: int, gid: int, claude_bin: str,
                   net: str = NET_NONE) -> list[str]:
    real = os.path.realpath(cwd)
    argv = ["docker", "run", "--rm", "-it", "--init",
            "--name", container_name(sid), "--label", f"{LABEL}={sid}",
            "--label", f"{NET_LABEL}={net}",
            "--network", network_name(sid),
            "--user", f"{uid}:{gid}", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--read-only",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
            "--tmpfs", f"{GUEST_HOME}:rw,nosuid,nodev,size=512m,uid={uid},gid={gid},mode=700",
            "--memory", GUEST_MEMORY,
            "--pids-limit", GUEST_PIDS_WEB if net == NET_WEB else GUEST_PIDS,
            "-e", f"HOME={GUEST_HOME}",
            "-e", f"TERM={os.environ.get('TERM') or 'xterm-256color'}",
            "-e", "LANG=C.UTF-8",
            "-e", f"ANTHROPIC_BASE_URL=http://{PROXY_ALIAS}:{PROXY_PORT}",
            # A placeholder so Claude sends *an* Authorization header; the proxy replaces it.
            "-e", "ANTHROPIC_AUTH_TOKEN=placeholder",
            "-e", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
            "-e", "DISABLE_AUTOUPDATER=1",
            "-v", f"{claude_bin}:{CLAUDE_MOUNT}:ro",
            "-v", f"{real}:{GUEST_WORKDIR}"]
    for k, v in guest_proxy_env(net).items():
        argv += ["-e", f"{k}={v}"]
    for e in readonly_entries(real):
        argv += ["-v", f"{os.path.join(real, e)}:{GUEST_WORKDIR}/{e}:ro"]
    for m in env_masks(real):
        argv += ["-v", f"/dev/null:{GUEST_WORKDIR}/{m}:ro"]
    argv += ["-w", GUEST_WORKDIR, guest_image(net), "sh", "-c", _GUEST_ENTRY]
    return argv


# --- preflight + launcher ----------------------------------------------------------------

def _docker(*args: str, timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def preflight(cwd: str, net: str = NET_NONE) -> str | None:
    """Why a sandboxed session can't start in ``cwd`` right now, or None. Fail closed:
    every missing piece is a refusal, never a fallback to an unsandboxed launch (and a
    ``web`` session missing its browser image never falls back to ``none`` or vice versa)."""
    if net not in NET_MODES:
        return f"unknown network mode {net!r}"
    why = workspace_refusal(cwd)
    if why:
        return why
    if not shutil.which("docker"):
        return "docker is not installed — the sandboxed session runs in a container"
    try:
        if _docker("info", "--format", "{{.ServerVersion}}").returncode != 0:
            return "docker is not reachable (is the daemon running, and are you in the docker group?)"
        if _docker("image", "inspect", image()).returncode != 0:
            return f"image {image()} is not present — run: docker pull {image()}"
        if net == NET_WEB and _docker("image", "inspect", web_image()).returncode != 0:
            return (f"the web-mode browser image {web_image()} is not present — build it: "
                    f"docker build -t {web_image()} {WEB_IMAGE_DIR}")
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"docker failed: {e}"
    if not claude_binary():
        return "the claude binary is not on PATH"
    if not token_file().is_file():
        return (f"no token file at {token_file()} — set CHELA_SHARE_SANDBOX_TOKEN_FILE "
                "(e.g. to a file holding a `claude setup-token` token)")
    return None


def cleanup(sid: str) -> None:
    """Remove the guest container, both sidecars and the network — idempotent. The web
    request log is KEPT for the operator to read afterwards."""
    for args in (("rm", "-f", container_name(sid)), ("rm", "-f", proxy_name(sid)),
                 ("rm", "-f", web_proxy_name(sid)), ("network", "rm", network_name(sid))):
        try:
            _docker(*args)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _hold(msg: str) -> None:
    """Show a refusal in the pane before it closes (no shell is ever offered)."""
    print(f"\n  chela sandboxed session: {msg}\n", flush=True)
    time.sleep(15)


def _web_steps(sid: str, uid: int, gid: int) -> list[list[str]]:
    return [web_proxy_run_argv(sid, uid, gid),
            ["docker", "network", "connect", "--alias", WEB_ALIAS,
             network_name(sid), web_proxy_name(sid)]]


def _prepare_web_log(sid: str) -> None:
    path = web_log_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.touch(mode=0o600, exist_ok=True)


def run(sid: str, cwd: str, net: str = NET_NONE) -> int:
    """The window's process: bring up network + proxy (+ the web proxy in ``web`` mode),
    run the guest container in the foreground on this pane's tty, and tear everything
    down when it exits."""
    if not _SID_RE.match(sid):
        _hold("invalid session id")
        return 2
    why = preflight(cwd, net)
    if why:
        _hold(f"refusing to start — {why}")
        return 1
    claude_bin = claude_binary() or ""

    def _term(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGHUP, _term)
    signal.signal(signal.SIGTERM, _term)
    uid, gid = os.getuid(), os.getgid()
    prune_session_dirs()
    try:
        # 0o700 on the root AND the session dir: they hold every guest reply on disk.
        session_root().mkdir(mode=0o700, parents=True, exist_ok=True)
        session_dir(sid).mkdir(mode=0o700, exist_ok=True)
    except OSError as e:
        _hold(f"refusing to start — cannot create {session_dir(sid)}: {e}")
        return 1
    steps = [network_create_argv(sid), proxy_run_argv(sid, uid, gid),
             ["docker", "network", "connect", "--alias", PROXY_ALIAS,
              network_name(sid), proxy_name(sid)]]
    try:
        if net == NET_WEB:
            _prepare_web_log(sid)
            steps += _web_steps(sid, uid, gid)
        for argv in steps:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            if p.returncode != 0:
                _hold(f"refusing to start — `{' '.join(argv[:3])}` failed: {p.stderr.strip()[:200]}")
                return 1
        if net == NET_WEB:
            print(f"\n  🌐 web access on — every request is logged to {web_log_path(sid)}\n",
                  flush=True)
        return subprocess.call(guest_run_argv(sid, cwd, uid, gid, claude_bin, net))
    finally:
        cleanup(sid)


# --- verification (the gate collab_stream asks before forwarding a keystroke) -----------

def pane_net_mode(argv: list[str]) -> str | None:
    """The network mode a launcher argv was started with, or None when it isn't the
    launcher's shape. A pre-CMX-418 launcher (no ``--net``) is a ``none`` session."""
    if len(argv) < 7 or argv[1:5] != ["-m", LAUNCH_MODULE, "run", "--id"]:
        return None
    if len(argv) == 7:
        return NET_NONE
    if len(argv) == 9 and argv[6] == "--net" and argv[7] in NET_MODES:
        return argv[7]
    return None


def verify_pane(argv: list[str], parent_comm: str,
                child_comms: list[str]) -> tuple[str, str, str] | str:
    """``(sid, cwd, net)`` when the pane's root process is exactly :func:`launcher_argv`'s
    shape, started by the tmux server itself (no shell in between) and with nothing but the
    docker client under it; otherwise the reason it is not."""
    net = pane_net_mode(argv)
    if net is None:
        return "the window is not running the sandboxed-session launcher"
    if not re.match(r"^python[0-9.]*$", os.path.basename(argv[0])):
        return "the launcher is not a python interpreter"
    sid, cwd = argv[5], argv[-1]
    if not _SID_RE.match(sid) or not os.path.isabs(cwd):
        return "the launcher's arguments are malformed"
    if not parent_comm.startswith("tmux"):
        return "the launcher was not started by tmux directly"
    if any(c != "docker" for c in child_comms):
        return "an unexpected process runs under the launcher"
    return sid, cwd, net


def _env_map(cfg: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for kv in cfg.get("Env") or []:
        k, _, v = kv.partition("=")
        out[k] = v
    return out


def verify_net_mode(info: dict, net: dict, sid: str, mode: str) -> str | None:
    """None when the guest's network-mode facts match ``mode`` (the mode the pane was
    LAUNCHED with): its label, its proxy env, and the exact set of containers on its
    network. A ``none`` session that gained a web sidecar or a proxy variable fails."""
    cfg = info.get("Config") or {}
    # A pre-CMX-418 guest carries no mode label; it was a `none` session by construction.
    if (cfg.get("Labels") or {}).get(NET_LABEL, NET_NONE) != mode:
        return "the container's network mode is not the one the session was launched with"
    env = _env_map(cfg)
    want = guest_proxy_env(mode)
    for k in PROXY_ENV_KEYS + ("NO_PROXY", "no_proxy"):
        if env.get(k) != want.get(k):
            return f"the container's proxy settings ({k}) do not match its network mode"
    members = {(c or {}).get("Name") for c in (net.get("Containers") or {}).values()}
    expected = {container_name(sid), proxy_name(sid)}
    if mode == NET_WEB:
        expected.add(web_proxy_name(sid))
    if members != expected:
        return "the session's network has unexpected members (a route it was not launched with)"
    return None


def verify_web_proxy(web: dict | None, sid: str, uid: int, gid: int) -> str | None:
    """None when ``docker inspect`` of the web sidecar shows :func:`web_proxy_run_argv`'s
    shape: this session's, unprivileged, read-only, running chela's filtering proxy from a
    read-only mount, and writing only its own log file."""
    if not web:
        return "the web egress proxy is not running"
    hc, cfg = web.get("HostConfig") or {}, web.get("Config") or {}
    if not (web.get("State") or {}).get("Running"):
        return "the web egress proxy is not running"
    labels = cfg.get("Labels") or {}
    if labels.get(LABEL) != sid or labels.get(NET_LABEL) != NET_WEB:
        return "the web egress proxy is not this session's"
    if hc.get("Privileged") or hc.get("CapAdd") or "ALL" not in (hc.get("CapDrop") or []) \
            or not hc.get("ReadonlyRootfs") or "no-new-privileges" not in (hc.get("SecurityOpt") or []):
        return "the web egress proxy has extra privileges"
    if cfg.get("User") != f"{uid}:{gid}":
        return "the web egress proxy does not run as the host user"
    if (cfg.get("Cmd") or [])[-1:] != [WEB_SCRIPT_MOUNT]:
        return "the web egress proxy is not running chela's filtering proxy"
    script = log_ok = False
    src_script = os.path.realpath(str(Path(__file__).with_name("share_web_proxy.py")))
    for m in web.get("Mounts") or []:
        src, dst, rw = os.path.realpath(m.get("Source", "")), m.get("Destination", ""), bool(m.get("RW"))
        if m.get("Type") != "bind":
            return f"unexpected {m.get('Type')} mount in the web egress proxy"
        if dst == WEB_SCRIPT_MOUNT and not rw and src == src_script:
            script = True
        elif dst == WEB_LOG_MOUNT and src == os.path.realpath(str(web_log_path(sid))):
            log_ok = True
        else:
            return f"unexpected mount {src} -> {dst} in the web egress proxy"
    if not (script and log_ok):
        return "the web egress proxy's mounts do not match"
    return None


def verify_container(info: dict, net: dict, sid: str, cwd: str, uid: int, gid: int,
                     mode: str = NET_NONE, web: dict | None = None) -> str | None:
    """None when ``docker inspect`` of the guest container and its network show exactly
    the shape :func:`guest_run_argv` / :func:`network_create_argv` create for ``mode``
    (and, in ``web`` mode, ``web`` — the sidecar — :func:`web_proxy_run_argv`'s); else
    why not."""
    real = os.path.realpath(cwd)
    hc = info.get("HostConfig") or {}
    cfg = info.get("Config") or {}
    if not (info.get("State") or {}).get("Running"):
        return "the sandbox container is not running"
    if (cfg.get("Labels") or {}).get(LABEL) != sid:
        return "the container is not this session's sandbox"
    if hc.get("Privileged") or hc.get("CapAdd") or "ALL" not in (hc.get("CapDrop") or []):
        return "the container has extra privileges"
    if not hc.get("ReadonlyRootfs"):
        return "the container's root filesystem is writable"
    if "no-new-privileges" not in (hc.get("SecurityOpt") or []):
        return "the container allows privilege escalation"
    if not hc.get("Memory") or not hc.get("PidsLimit"):
        return "the container has no memory or pid cap"
    if cfg.get("User") != f"{uid}:{gid}":
        return "the container does not run as the host user"
    if hc.get("NetworkMode") != network_name(sid) or \
            set((info.get("NetworkSettings") or {}).get("Networks") or {}) != {network_name(sid)}:
        return "the container is not on its isolated network"
    if not net.get("Internal") or \
            (net.get("Options") or {}).get("com.docker.network.bridge.inhibit_ipv4") != "true":
        return "the container's network can reach the host or the internet"
    workspace = claude = False
    for m in info.get("Mounts") or []:
        src, dst, rw = m.get("Source", ""), m.get("Destination", ""), bool(m.get("RW"))
        if m.get("Type") != "bind":
            return f"unexpected {m.get('Type')} mount at {dst}"
        if dst == GUEST_WORKDIR and os.path.realpath(src) == real and rw:
            workspace = True
        elif dst == CLAUDE_MOUNT and not rw:
            claude = True
        elif dst.startswith(GUEST_WORKDIR + "/") and not rw and (
                src == "/dev/null" or os.path.realpath(src).startswith(real + os.sep)):
            continue   # a read-only mask / protected entry inside the workspace
        else:
            return f"unexpected mount {src} -> {dst}"
    if not (workspace and claude):
        return "the container's mounts do not match a sandboxed session"
    why = verify_net_mode(info, net, sid, mode)
    if why:
        return why
    if mode == NET_WEB:
        return verify_web_proxy(web, sid, uid, gid)
    if web is not None:
        return "a web egress proxy runs for a session launched without web access"
    return None


def _pane_root(wid: str) -> int | str:
    """The pid of the window's only pane, or why it can't be determined."""
    try:
        p = subprocess.run(
            ["tmux", "list-panes", "-t", f"{config.current_session()}:{wid}",
             "-F", "#{pane_pid}\t#{pane_dead}"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return "tmux is unreachable"
    rows = [r.split("\t") for r in p.stdout.splitlines() if r.strip()]
    if p.returncode != 0 or not rows:
        return "no such window"
    if len(rows) != 1:
        return "the window has more than one pane"
    pid, dead = rows[0]
    if dead == "1" or not pid.isdigit():
        return "the pane is dead"
    return int(pid)


def _proc_shape(pid: int) -> tuple[list[str], str, list[str]]:
    """``(argv, parent comm, child comms)`` for ``pid`` from /proc. Raises OSError when
    /proc is unreadable — the caller treats that as NOT sandboxed."""
    with open(f"/proc/{pid}/cmdline", "rb") as f:
        argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
    with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
        ppid = int(f.read().rsplit(")", 1)[1].split()[1])
    with open(f"/proc/{ppid}/comm", encoding="utf-8") as f:
        parent = f.read().strip()
    kids: list[str] = []
    with open(f"/proc/{pid}/task/{pid}/children", encoding="utf-8") as f:
        for c in f.read().split():
            with open(f"/proc/{c}/comm", encoding="utf-8") as g:
                kids.append(g.read().strip())
    return argv, parent, kids


def _inspect(sid: str) -> tuple[dict, dict, dict | None] | str:
    """``(guest, network, web sidecar or None)`` from ``docker inspect``. The web sidecar
    is looked up in EVERY mode — its presence is what a ``none`` session must not have."""
    try:
        c = _docker("inspect", "--type", "container", container_name(sid), timeout=10)
        n = _docker("network", "inspect", network_name(sid), timeout=10)
        w = _docker("inspect", "--type", "container", web_proxy_name(sid), timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "docker is unreachable"
    if c.returncode != 0 or n.returncode != 0:
        return "the sandbox container is not running"
    try:
        web = json.loads(w.stdout)[0] if w.returncode == 0 else None
        return json.loads(c.stdout)[0], json.loads(n.stdout)[0], web
    except (ValueError, IndexError):
        return "docker inspect returned nothing usable"


def pane_identity(wid: str) -> tuple[int, str, str, str] | str:
    """``(pane pid, sid, cwd, net mode)`` when the window's pane is the sandboxed-session launcher
    (the cheap half of :func:`check_share_session`: one tmux query + /proc, no docker);
    otherwise the reason it is not. ``collab_stream`` reads this before EVERY output frame
    of a typing share, so a swapped pane process is caught before its first frame leaves."""
    root = _pane_root(wid)
    if isinstance(root, str):
        return root
    try:
        argv, parent, kids = _proc_shape(root)
    except (OSError, ValueError, IndexError):
        return "the pane's process can't be read"
    shape = verify_pane(argv, parent, kids)
    if isinstance(shape, str):
        return shape
    return root, shape[0], shape[1], shape[2]


def _check(wid: str) -> tuple[str | None, str]:
    """``(sid, "")`` only when the LIVE window verifies as a sandboxed session; otherwise
    ``(None, reason)``. Every unknown — no tmux, unreadable /proc, docker down — is None."""
    try:
        ident = pane_identity(wid)
        if isinstance(ident, str):
            return None, ident
        _root, sid, cwd, mode = ident
        got = _inspect(sid)
        if isinstance(got, str):
            return None, got
        why = verify_container(got[0], got[1], sid, cwd, os.getuid(), os.getgid(),
                               mode=mode, web=got[2])
        return (None, why) if why else (sid, "")
    except Exception as e:  # noqa: BLE001 — fail closed on anything unexpected
        log.warning("share_sandbox: check of %s failed: %r", wid, e)
        return None, "the sandbox could not be verified"


def check_share_session(wid: str) -> tuple[bool, str]:
    """``(True, "")`` only when the LIVE window verifies as a sandboxed session; otherwise
    ``(False, reason)``. Every unknown — no tmux, unreadable /proc, docker down — is False."""
    sid, why = _check(wid)
    return (True, "") if sid else (False, why)


def share_session_id(wid: str) -> str | None:
    """The session id of a window that verifies LIVE as a sandboxed session, else None —
    what the Telegram relay and the doctor key the session's outbox on."""
    return _check(wid)[0]


def is_sandboxed_share_session(wid: str) -> bool:
    return check_share_session(wid)[0]


def window_net_mode(wid: str) -> str | None:
    """DISPLAY ONLY (the pane's "🌐 web" chip): the network mode the window's launcher
    was started with, read from its argv; None when it isn't a sandboxed launcher. The
    security gate is :func:`check_share_session`, never this."""
    try:
        root = _pane_root(wid)
        if isinstance(root, str):
            return None
        with open(f"/proc/{root}/cmdline", "rb") as f:
            argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        return pane_net_mode(argv)
    except (OSError, ValueError):
        return None


NOT_SANDBOXED_HINT = ("this window isn't a sandboxed session — start one from "
                      "New session → Sandboxed")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m chela.share_sandbox")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="(the window's own process) run a sandboxed session")
    r.add_argument("--id", required=True)
    r.add_argument("--net", choices=NET_MODES, default=NET_NONE)
    r.add_argument("cwd")
    c = sub.add_parser("check", help="verify a window is a sandboxed session")
    c.add_argument("wid")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run(a.id, a.cwd, a.net)
    ok, why = check_share_session(a.wid)
    print("sandboxed" if ok else f"NOT sandboxed: {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
