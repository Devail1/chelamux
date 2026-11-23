"""CMX-469: the terminal wall's supervisor runs on bash 3.2, the /bin/bash macOS ships.

`scripts/agent-terminals.sh` used `declare -A` (bash 4), so on a Mac it died before
spawning a single ttyd unless Homebrew bash came first on PATH. Two checks keep it on 3.2:

* a guard over the source of it and of `scripts/chela-env.sh` (which it sources), rejecting
  the bash-4-only constructs one by one;
* the supervisor itself, driven end to end with stub `tmux` / `ttyd` / python: windows come,
  windows go, the port map follows, SIGTERM reaps every ttyd. It runs under this machine's
  bash, and under a real bash 3.2 (the `bash:3.2` container image) when that image is
  already present locally; nothing is pulled from inside the suite.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ("scripts/agent-terminals.sh", "scripts/chela-env.sh")

# (pattern, what it is). Comments are stripped before matching.
BASH4_ONLY = [
    (r"\bdeclare\s+-[A-Za-z]*A", "associative array (declare -A)"),
    (r"\blocal\s+-[A-Za-z]*A", "associative array (local -A)"),
    (r"\btypeset\s+-[A-Za-z]*A", "associative array (typeset -A)"),
    (r"\b(declare|local|typeset)\s+-[A-Za-z]*n\b", "nameref (declare -n)"),
    (r"\b(mapfile|readarray)\b", "mapfile/readarray"),
    (r"\$\{[^}]*(,,|\^\^)", "case-modifying expansion ${v,,} / ${v^^}"),
    (r"\$\{[^}]*[^,],\}|\$\{[^}]*[^\^]\^\}", "case-modifying expansion ${v,} / ${v^}"),
    (r"\$\{[^}]*@[QEPAaKkUuL]\}", "transformation ${v@Q}"),
    (r"\[\[\s+-v\s", "[[ -v ]]"),
    (r"\bwait\s+-n\b", "wait -n"),
    (r"\|&", "|& (pipe stderr)"),
    (r"&>>", "&>> (append both)"),
    (r"\bcoproc\b", "coproc"),
    (r"\$\{[^}]*\[-\d+\]\}", "negative array index"),
    (r"\bshopt\s+-s\s+(globstar|lastpipe|direxpand)", "bash-4 shopt"),
    (r"\$EPOCHSECONDS|\$EPOCHREALTIME|\$\{EPOCH", "EPOCHSECONDS (bash 5)"),
    (r";;&|;&", "case fall-through ;& / ;;&"),
]


def _code_lines(text: str) -> list[tuple[int, str]]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append((n, re.sub(r"\s#\s.*$", "", line)))
    return out


def _bash4_hits(text: str) -> list[str]:
    return [f"{n}: {what}: {line.strip()}"
            for n, line in _code_lines(text)
            for pattern, what in BASH4_ONLY if re.search(pattern, line)]


@pytest.mark.parametrize("rel", SCRIPTS)
def test_no_bash4_only_syntax(rel):
    hits = _bash4_hits((ROOT / rel).read_text())
    assert not hits, f"{rel} must run on bash 3.2 (macOS /bin/bash):\n" + "\n".join(hits)


@pytest.mark.parametrize("snippet", [
    "declare -A PORT_OF PID_OF",
    "    local -A seen",
    "declare -n ref=x",
    "mapfile -t lines < f",
    'x="${v,,}"',
    'x="${v^^}"',
    'x="${v@Q}"',
    "[[ -v FOO ]] && echo",
    "wait -n",
    "cmd |& tee log",
    "cmd &>> log",
    "coproc foo",
    'echo "${arr[-1]}"',
    "shopt -s globstar",
    "x=$EPOCHSECONDS",
    "  a) echo;;&",
])
def test_the_guard_catches_each_bash4_construct(snippet):
    assert _bash4_hits(snippet), snippet


@pytest.mark.parametrize("snippet", [
    "# declare -A in a comment is fine",
    'at_get() { local n="AT_$1_${2//[!A-Za-z0-9_]/_}"; printf \'%s\' "${!n:-}"; }',
    'printf -v "AT_$1" \'%s\' "$3"',
    'json+=","',
    'while IFS=$\'\\t\' read -r wid name; do',
    'case "$v" in a) echo ;; esac',
    'sleep 1 & NAP_PID=$!',
    '[ -n "${!key+set}" ] || export "$key=$value"',
    "cmd 2>&1 | tee log",
    "cmd >/dev/null 2>&1 &",
])
def test_the_guard_passes_bash32_code(snippet):
    assert not _bash4_hits(snippet), snippet


# --- the supervisor, end to end under stubs ------------------------------------------------

def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _layout(tmp: Path) -> dict:
    """A checkout-shaped copy of the two scripts plus stub tmux/ttyd/python; returns env."""
    (tmp / "scripts").mkdir()
    for rel in SCRIPTS:
        shutil.copy2(ROOT / rel, tmp / rel)
    bin_ = tmp / "bin"
    bin_.mkdir()
    # tmux: logs every call; `has-session` succeeds only while the `session` file exists;
    # every other call succeeds and does nothing.
    (tmp / "session").write_text("")
    _stub(bin_ / "tmux",
          f'echo "$*" >> "{tmp}/tmux.log"\n'
          f'[ "$1" = has-session ] && {{ [ -e "{tmp}/session" ]; exit $?; }}\n'
          "exit 0\n")
    # ttyd: record its port and pid, then idle until killed.
    _stub(bin_ / "ttyd",
          'while [ $# -gt 0 ]; do [ "$1" = --port ] && port="$2"; shift; done\n'
          f'echo $$ > "{tmp}/pids/$port"\n'
          "exec sleep 1000\n")
    (tmp / "pids").mkdir()
    # python: list_windows reads the windows file. A snippet that names a session create
    # (the old `ensure_session` heal, or `open_window`) is logged to py.log and "creates"
    # the session, so a test can prove the supervisor never runs one (CMX-473).
    _stub(bin_ / "py",
          f'case "$2" in *ensure_session*|*open_window*) echo "create $3" >> "{tmp}/py.log"; '
          f': > "{tmp}/session"; exit 0 ;; esac\n'
          f'cat "{tmp}/windows"\n')
    chela_dir = tmp / "chela"
    chela_dir.mkdir()
    # The base port comes from chela.env, so chela-env.sh's loader runs under the shell too.
    (chela_dir / "chela.env").write_text("# config\nexport CHELA_TERM_BASE='6200'\n")
    return {
        "PATH": f"{bin_}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp),
        "CHELA_DIR": str(chela_dir),
        "CHELA_TMUX_SESSION": "chela",
        "CHELA_TERM_POLL": "1",
        "PYTHON": str(bin_ / "py"),
        "TTYD": str(bin_ / "ttyd"),
    }


def _windows(tmp: Path, *wids: str) -> None:
    (tmp / "windows").write_text("".join(f"{w}\tname{w[1:]}\n" for w in wids))


def _wait_map(tmp: Path, want: dict, timeout: float = 30) -> None:
    path = tmp / "chela" / "agent_terminals.json"
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        try:
            seen = json.loads(path.read_text())
        except (OSError, ValueError):
            seen = None
        if seen == want:
            return
        time.sleep(0.2)
    raise AssertionError(f"map never became {want}; last seen {seen}")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _bash32_runner() -> list[str] | None:
    docker = shutil.which("docker")
    if docker is None:
        return None
    try:
        found = subprocess.run([docker, "image", "inspect", "bash:3.2"],
                               capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [docker] if found else None


def _shells():
    yield pytest.param("host", id="host-bash")
    yield pytest.param("bash:3.2", id="bash-3.2", marks=pytest.mark.skipif(
        _bash32_runner() is None, reason="no local `bash:3.2` image (docker pull bash:3.2)"))


def _start(shell: str, tmp: Path, env: dict) -> subprocess.Popen:
    script = str(tmp / "scripts" / "agent-terminals.sh")
    if shell == "host":
        return subprocess.Popen(["bash", script], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
    # Same paths inside the container, same uid, the host's pid namespace so the ttyd
    # stubs' pids are this test's to check.
    argv = ["docker", "run", "--rm", "-i", "--pid=host", "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{tmp}:{tmp}"]
    for k, v in env.items():
        argv += ["-e", f"{k}={v}"]
    argv += ["bash:3.2", "bash", "-c", f'echo "bash $BASH_VERSION"; exec bash {script}']
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


@pytest.mark.parametrize("shell", list(_shells()))
def test_supervisor_follows_windows_and_reaps_on_sigterm(shell, tmp_path):
    env = _layout(tmp_path)
    _windows(tmp_path, "@1", "@2")
    proc = _start(shell, tmp_path, env)
    try:
        _wait_map(tmp_path, {"@1": 6200, "@2": 6201})
        # @1 closes, @3 opens in one tick. Additions run before removals, so @3 takes the
        # next free port; once @1 is gone, @4 gets its port back (the lowest free one).
        _windows(tmp_path, "@2", "@3")
        _wait_map(tmp_path, {"@2": 6201, "@3": 6202})
        _windows(tmp_path, "@2", "@3", "@4")
        _wait_map(tmp_path, {"@2": 6201, "@3": 6202, "@4": 6200})
        # A ttyd that dies is respawned on the port it had.
        first = int((tmp_path / "pids" / "6201").read_text())
        os.kill(first, signal.SIGKILL)
        deadline = time.monotonic() + 30
        while int((tmp_path / "pids" / "6201").read_text() or 0) == first:
            assert time.monotonic() < deadline, "the dead ttyd was never respawned"
            time.sleep(0.2)
        pids = [int((tmp_path / "pids" / p).read_text()) for p in ("6200", "6201", "6202")]
        assert all(_alive(p) for p in pids)
    finally:
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=30)
    if shell != "host":
        assert "bash 3.2" in out, out
    assert "+ name1 (@1) -> :6200" in out, out
    assert "- @1 (window gone)" in out, out
    assert "ttyd died, respawn" in out, out
    assert "declare" not in out, out          # bash 3.2's "declare: -A: invalid option"
    assert not (tmp_path / "chela" / "agent_terminals.json").exists()
    deadline = time.monotonic() + 10
    while any(_alive(p) for p in pids):
        assert time.monotonic() < deadline, "SIGTERM left a ttyd running"
        time.sleep(0.2)


@pytest.mark.parametrize("shell", list(_shells()))
def test_an_exported_value_beats_chela_env(shell, tmp_path):
    """chela-env.sh's `${!key+set}` (indirect expansion) holds on the shell too."""
    env = _layout(tmp_path)
    env["CHELA_TERM_BASE"] = "6300"
    _windows(tmp_path, "@4")
    proc = _start(shell, tmp_path, env)
    try:
        _wait_map(tmp_path, {"@4": 6300})
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=30)


@pytest.mark.parametrize("shell", list(_shells()))
@pytest.mark.parametrize("heal", [None, "true"])
def test_a_missing_session_is_no_windows_and_is_never_created(shell, heal, tmp_path):
    """CMX-473: the supervisor never creates the tmux session — not even with the retired
    CHELA_TERM_HEAL=true left in an env. A missing session reads as NO windows: it keeps
    polling (never exits), serves the session once a spawn creates it, and when the
    session ends (its last window closed) it reaps every ttyd and empties the map instead
    of holding the stale set."""
    env = _layout(tmp_path)
    if heal is not None:
        env["CHELA_TERM_HEAL"] = heal
    (tmp_path / "session").unlink()
    _windows(tmp_path, "@5")
    proc = _start(shell, tmp_path, env)
    try:
        deadline = time.monotonic() + 30
        log = tmp_path / "tmux.log"
        while not (log.exists() and log.read_text().count("has-session") >= 3):
            assert time.monotonic() < deadline, "the supervisor never polled for the session"
            time.sleep(0.2)
        assert proc.poll() is None, "a missing session made the supervisor exit"
        assert not (tmp_path / "py.log").exists()
        assert "new-session" not in log.read_text()
        _wait_map(tmp_path, {})
        (tmp_path / "session").write_text("")      # a spawn creates it
        _wait_map(tmp_path, {"@5": 6200})
        pid = int((tmp_path / "pids" / "6200").read_text())
        (tmp_path / "session").unlink()            # its last window closed
        _wait_map(tmp_path, {})
        deadline = time.monotonic() + 10
        while _alive(pid):
            assert time.monotonic() < deadline, "the ended session's ttyd was never reaped"
            time.sleep(0.2)
    finally:
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=30)
    assert out.count("no tmux session 'chela' — no windows") == 2, out   # once per transition
    assert "tmux session 'chela' is back" in out, out
    assert "- @5 (window gone)" in out, out
    assert not (tmp_path / "py.log").exists()
    assert "new-session" not in (tmp_path / "tmux.log").read_text()


def test_one_failed_session_check_does_not_reap_the_wall(tmp_path):
    """A single has-session failure (a busy server) must not tear every tile down: the set
    is kept for that tick and only a session still absent on the next tick is reaped."""
    env = _layout(tmp_path)
    _windows(tmp_path, "@8")
    proc = _start("host", tmp_path, env)
    try:
        _wait_map(tmp_path, {"@8": 6200})
        pid = int((tmp_path / "pids" / "6200").read_text())
        log = tmp_path / "tmux.log"
        (tmp_path / "session").unlink()
        n = log.read_text().count("has-session")
        deadline = time.monotonic() + 10
        while log.read_text().count("has-session") < n + 1:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        (tmp_path / "session").write_text("")     # back before the second check
        time.sleep(3)
        assert _alive(pid), "one failed session check reaped a live tile"
        assert json.loads((tmp_path / "chela" / "agent_terminals.json").read_text()) == {"@8": 6200}
    finally:
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=30)
    assert "- @8 (window gone)" not in out, out


def test_negative_control_the_old_empty_guard_held_a_dead_sessions_ttyds(tmp_path):
    """The guard that skips an empty tick while ttyds run must NOT apply when the session is
    absent: with it applied (the old reading), the ended session's ttyd is held forever."""
    env = _layout(tmp_path)
    script = tmp_path / "scripts" / "agent-terminals.sh"
    text = script.read_text()
    held = text.replace('( "${SESSION_STATE}" == present || ABSENT_TICKS -lt 2 )',
                        'true')
    assert held != text
    script.write_text(held)
    _windows(tmp_path, "@7")
    proc = _start("host", tmp_path, env)
    try:
        _wait_map(tmp_path, {"@7": 6200})
        (tmp_path / "session").unlink()
        with pytest.raises(AssertionError, match="map never became"):
            _wait_map(tmp_path, {}, timeout=4)
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=30)
