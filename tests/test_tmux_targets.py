"""CMX-470: every tmux target chela builds from a SESSION NAME is an exact-match target.

tmux resolves a bare ``-t chela`` by PREFIX: with no ``chela`` session but a
``chela-services`` one, it silently targets ``chela-services``. ``=chela`` (and
``=chela:@3``) accept only the exact name. ``config.session_target`` /
``config.window_target`` build those; this guard fails on any call site that builds a
bare one instead.
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


def bare_targets(source: str, filename: str = "<src>") -> list[str]:
    """Every bare session target in ``source``, as ``"<file>:<line>: <code>"``."""
    tree = ast.parse(source, filename)
    lines = source.splitlines()
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if _bare_fstring_target(node):
            hits.append((node.lineno, "f-string session:window target"))
        seq = None
        if isinstance(node, (ast.List, ast.Tuple)):
            seq = node.elts
        elif isinstance(node, ast.Call):
            seq = node.args
        if not seq:
            continue
        for flag, value in zip(seq, seq[1:]):
            if (isinstance(flag, ast.Constant) and flag.value == "-t"
                    and _is_session_expr(value)):
                hits.append((value.lineno, "bare -t <session>"))
    return [f"{filename}:{n}: {why}: {lines[n - 1].strip()}" for n, why in sorted(set(hits))]


# A shell `-t` whose value is a session variable, quoted or not, with no `=` in front.
_SH_BARE = re.compile(r'-t\s+"?\$\{?(?:TMUX_SESSION|SESSION|CHELA_TMUX_SESSION)\b')


def bare_shell_targets(text: str, filename: str = "<sh>") -> list[str]:
    return [f"{filename}:{n}: {line.strip()}"
            for n, line in enumerate(text.splitlines(), 1)
            if not line.lstrip().startswith("#") and _SH_BARE.search(line)]


def test_no_python_call_site_builds_a_bare_session_target():
    hits: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        hits += bare_targets(path.read_text(), str(path.relative_to(ROOT)))
    assert not hits, ("build session targets with config.session_target / "
                      "config.window_target (exact '=name'):\n" + "\n".join(hits))


def test_no_shell_script_builds_a_bare_session_target():
    hits: list[str] = []
    for path in sorted([*SCRIPTS.rglob("*.sh"), *PACKAGE.rglob("*.sh")]):
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
])
def test_guard_passes_an_exact_or_non_session_target(src):
    assert bare_targets(src) == []


@pytest.mark.parametrize("line, bare", [
    ('tmux has-session -t "${TMUX_SESSION}" 2>/dev/null', True),
    ('tmux has-session -t "$TMUX_SESSION"', True),
    ('tmux has-session -t ${TMUX_SESSION}', True),
    ('tmux new-session -A -s "${grp}" -t "${TMUX_SESSION}"', True),
    ('tmux has-session -t "=${TMUX_SESSION}" 2>/dev/null', False),
    ('tmux kill-session -t "${WEBTERM_PREFIX}x"', False),
    ('# tmux has-session -t "${TMUX_SESSION}"', False),
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
