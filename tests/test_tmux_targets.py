"""CMX-470: every tmux target chela builds from a SESSION NAME is an exact-match target.

tmux resolves a bare ``-t chela`` by PREFIX: with no ``chela`` session but a
``chela-services`` one, it silently targets ``chela-services``. ``=chela`` (and
``=chela:@3``) accept only the exact name. ``config.session_target`` /
``config.window_target`` build those; this guard fails on any call site that builds a
bare one instead.

⚠️ The Python guard is a TRIPWIRE, not a proof. It recognises a session expression by NAME
(``session``/``sess``/``session_name``/``TMUX_SESSION``/``tmux_session`` or a
``current_session()`` call), in the shapes ``-t <session>``, ``f"{session}:…"``,
``session + ":…"``, ``"%s:…" % (session, …)`` and ``"{}:…".format(session, …)``. A session
bound to any other name slips past it — the real-tmux tests in
``tests/test_service_session.py`` are what prove the behaviour.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from chela import config

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "chela"
SCRIPTS = ROOT / "scripts"

# Names a session lives under at chela's call sites. A target built from any of these, or
# from ``current_session()``, names a session and must go through the helpers.
_SESSION_NAMES = {"session", "sess", "session_name", "TMUX_SESSION", "tmux_session"}


def _is_session_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _SESSION_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in _SESSION_NAMES
    if isinstance(node, ast.Call):
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        return name == "current_session"
    return False


def _bare_fstring_target(node: ast.AST) -> bool:
    """``f"{session}:..."`` — a session-qualified target with no ``=`` in front."""
    return (isinstance(node, ast.JoinedStr) and len(node.values) >= 2
            and isinstance(node.values[0], ast.FormattedValue)
            and _is_session_expr(node.values[0].value)
            and isinstance(node.values[1], ast.Constant)
            and str(node.values[1].value).startswith(":"))


def _leftmost(node: ast.AST) -> ast.AST:
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        node = node.left
    return node


def _second_operand(node: ast.BinOp) -> ast.AST | None:
    """For ``a + b + c`` the operand right after the leftmost one."""
    while isinstance(node.left, ast.BinOp) and isinstance(node.left.op, ast.Add):
        node = node.left
    return node.right


def _starts_with_colon(node: ast.AST | None) -> bool:
    if isinstance(node, ast.Constant):
        return str(node.value).startswith(":")
    if isinstance(node, ast.JoinedStr) and node.values:
        return _starts_with_colon(node.values[0])
    return False


def _bare_concat_target(node: ast.AST) -> bool:
    """``session + ":" + wid`` / ``session + f":{wid}"``."""
    return (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
            and _is_session_expr(_leftmost(node))
            and _starts_with_colon(_second_operand(node)))


def _bare_format_target(node: ast.AST) -> bool:
    """``"%s:%s" % (session, wid)`` / ``"{}:{}".format(session, wid)``."""
    if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)):
        args = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
        return (node.left.value.startswith("%s:") and bool(args)
                and _is_session_expr(args[0]))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format" and isinstance(node.func.value, ast.Constant)
            and isinstance(node.func.value.value, str)):
        fmt = node.func.value.value
        return (fmt.startswith(("{}:", "{0}:")) and bool(node.args)
                and _is_session_expr(node.args[0]))
    return False


def _is_bare_session_value(node: ast.AST) -> bool:
    """A ``-t`` VALUE naming a session with no ``=``: the session itself, ``f"{session}"``,
    or ``session + …`` / ``str(session)``."""
    if _is_session_expr(node):
        return True
    if (isinstance(node, ast.JoinedStr) and node.values
            and isinstance(node.values[0], ast.FormattedValue)
            and _is_session_expr(node.values[0].value)):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_session_expr(_leftmost(node))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "str" and len(node.args) == 1):
        return _is_session_expr(node.args[0])
    return False


def bare_targets(source: str, filename: str = "<src>") -> list[str]:
    """Every bare session target in ``source``, as ``"<file>:<line>: <code>"``."""
    tree = ast.parse(source, filename)
    lines = source.splitlines()
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if _bare_fstring_target(node):
            hits.append((node.lineno, "f-string session:window target"))
        if _bare_concat_target(node):
            hits.append((node.lineno, "concatenated session:window target"))
        if _bare_format_target(node):
            hits.append((node.lineno, "%/format session:window target"))
        seq = None
        if isinstance(node, (ast.List, ast.Tuple)):
            seq = node.elts
        elif isinstance(node, ast.Call):
            seq = node.args
        if not seq:
            continue
        for flag, value in zip(seq, seq[1:]):
            if (isinstance(flag, ast.Constant) and flag.value == "-t"
                    and _is_bare_session_value(value)):
                hits.append((value.lineno, "bare -t <session>"))
    return [f"{filename}:{n}: {why}: {lines[n - 1].strip()}" for n, why in sorted(set(hits))]


# A shell `-t` whose value is a session variable, quoted or not, with no `=` in front.
_SH_BARE = re.compile(r'-t\s+"?\$\{?(?:TMUX_SESSION|SESSION|CHELA_TMUX_SESSION)\b')

# tmux subcommands whose `-t` names a SESSION (or a session-qualified window). For these
# ANY variable-built target must be exact, whatever the variable is called: the webterm
# viewer session for `@1` is `webterm_chela__1`, and a bare `kill-session -t` on it, once it
# is gone, prefix-matches `@10`'s `webterm_chela__10` and kills ANOTHER agent's terminal.
_SH_SESSION_CMDS = re.compile(
    r"\btmux\b(?:\s+-\S+)*\s+(?P<cmd>has-session|has|kill-session|new-session|new|"
    r"attach-session|attach|a|list-windows|lsw|list-clients|lsc|switch-client|switchc|"
    r"rename-session|new-window|neww)\b(?P<rest>.*)")
_SH_VAR_TARGET = re.compile(r'(?:^|\s)-t\s+"?\$')


def bare_shell_targets(text: str, filename: str = "<sh>") -> list[str]:
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        m = _SH_SESSION_CMDS.search(line)
        if _SH_BARE.search(line) or (m and _SH_VAR_TARGET.search(m.group("rest"))):
            hits.append(f"{filename}:{n}: {line.strip()}")
    return hits


def test_no_python_call_site_builds_a_bare_session_target():
    hits: list[str] = []
    paths = sorted(PACKAGE.rglob("*.py"))
    # the scan is not vacuous: it reaches the modules that build targets
    for must in ("discovery.py", "dispatcher.py", "messenger.py", "dashboard/app.py"):
        assert PACKAGE / must in paths, must
    for path in paths:
        hits += bare_targets(path.read_text(), str(path.relative_to(ROOT)))
    assert not hits, ("build session targets with config.session_target / "
                      "config.window_target (exact '=name'):\n" + "\n".join(hits))


def test_no_shell_script_builds_a_bare_session_target():
    hits: list[str] = []
    paths = sorted([*SCRIPTS.rglob("*.sh"), *PACKAGE.rglob("*.sh")])
    # the scan is not vacuous: the one script that builds session targets is in it
    assert SCRIPTS / "agent-terminals.sh" in paths
    for path in paths:
        hits += bare_shell_targets(path.read_text(), str(path.relative_to(ROOT)))
    assert not hits, "use -t \"=${TMUX_SESSION}\":\n" + "\n".join(hits)


@pytest.mark.parametrize("src", [
    '["tmux", "list-windows", "-t", session]',
    '["tmux", "list-windows", "-t", TMUX_SESSION]',
    '["tmux", "list-windows", "-t", config.TMUX_SESSION]',
    '["tmux", "list-windows", "-t", config.current_session()]',
    '("tmux", "has-session", "-t", current_session())',
    '_tmux("has-session", "-t", session)',
    'f"{session}:{wid}"',
    'f"{TMUX_SESSION}:"',
    'f"{config.current_session()}:{wid}"',
    '["tmux", "kill-window", "-t", f"{session}:@3"]',
    'session + ":" + wid',
    'config.current_session() + ":" + wid',
    'TMUX_SESSION + f":{wid}"',
    '"%s:%s" % (session, wid)',
    '"{}:{}".format(TMUX_SESSION, wid)',
    '["tmux", "has-session", "-t", f"{session}"]',
    '["tmux", "has-session", "-t", str(session)]',
    '["tmux", "list-windows", "-t", session + ""]',
])
def test_guard_flags_a_bare_session_target(src):
    assert bare_targets(src)


@pytest.mark.parametrize("src", [
    '["tmux", "list-windows", "-t", config.session_target(session)]',
    '["tmux", "list-windows", "-t", config.session_target()]',
    '["tmux", "kill-window", "-t", config.window_target(wid, session)]',
    '["tmux", "display-message", "-p", "-t", pane, "#{session_name}"]',
    '["tmux", "send-keys", "-t", wid, "Enter"]',
    '["tmux", "new-session", "-A", "-d", "-s", session]',
    'f"={session}:{wid}"',
    'f"{session_dir(sid)}:/mnt"',
    'f"{session}-{wid}"',
    '"=" + session + ":" + wid',
    'session + "-" + wid',
    '"%s-%s" % (session, wid)',
    '"={}:{}".format(session, wid)',
    '["tmux", "has-session", "-t", f"={session}"]',
])
def test_guard_passes_an_exact_or_non_session_target(src):
    assert bare_targets(src) == []


@pytest.mark.parametrize("line, bare", [
    ('tmux has-session -t "${TMUX_SESSION}" 2>/dev/null', True),
    ('tmux has-session -t "$TMUX_SESSION"', True),
    ('tmux has-session -t ${TMUX_SESSION}', True),
    ('tmux new-session -A -s "${grp}" -t "${TMUX_SESSION}"', True),
    ('tmux has-session -t "=${TMUX_SESSION}" 2>/dev/null', False),
    ('# tmux has-session -t "${TMUX_SESSION}"', False),
    # any variable naming a session, whatever it is called
    ('tmux kill-session -t "${WEBTERM_PREFIX}$(sanitize "$wid")" 2>/dev/null', True),
    ('tmux kill-session -t "$grp"', True),
    ('tmux -u new-session -A -s "${grp}" -t "${SESS}"', True),
    ('tmux list-windows -t "$s" -F "#W"', True),
    ('tmux kill-session -t "=${WEBTERM_PREFIX}$(sanitize "$wid")" 2>/dev/null', False),
    ('tmux -u new-session -A -s "${grp}" -t "=${TMUX_SESSION}"', False),
    # pane / window targets are not session names
    ('tmux display-message -t "$TMUX_PANE" -p "#W"', False),
    ('command tmux set-window-option -t "$_chela_t" @x 1', False),
    ('tmux select-window -t "${wid}"', False),
    # names read back from list-sessions, then killed: no `-t $var` on the line
    ('tmux list-sessions | xargs -r -n1 tmux kill-session -t 2>/dev/null', False),
])
def test_shell_guard_fixtures(line, bare):
    assert bool(bare_shell_targets(line)) is bare


# --- the helpers themselves: literal expected values, never the module's own constant ---

@pytest.mark.parametrize("session, want", [
    ("chela", "=chela"),
    ("chela-services", "=chela-services"),
    ("=chela", "=chela"),               # already exact: never doubled to "==chela"
    ("myteam", "=myteam"),
])
def test_session_target_is_exact(session, want):
    assert config.session_target(session) == want


@pytest.mark.parametrize("window, session, want", [
    ("@3", "chela", "=chela:@3"),
    ("agent-1", "chela", "=chela:agent-1"),
    ("", "chela", "=chela:"),
    ("@3", "=chela", "=chela:@3"),
])
def test_window_target_is_exact(window, session, want):
    assert config.window_target(window, session) == want


def test_helpers_default_to_the_current_session(monkeypatch):
    monkeypatch.setenv("CHELA_TMUX_SESSION", "fleet")
    assert config.session_target() == "=fleet"
    assert config.window_target("@7") == "=fleet:@7"
