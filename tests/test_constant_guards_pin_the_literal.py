"""DEFEAT_SHAPES #5, "asserting a source constant instead of the rendered value" — found a
fifth time in `tests/test_diffsurface.py` (CMX-299 rework round 10, PR #373):
`assert all(t == diffsurface._GIT_TIMEOUT for t in calls)` reads the SAME symbol the
mutation (`_GIT_TIMEOUT = 15` -> `_GIT_TIMEOUT = None`) edits, so both sides move together
and the assertion stays green after the bound is removed. Round 10 fixed that one constant
by hand; round 11 found the next one over (`_UNTRACKED_READ_CAP`) the same way, one at a
time, because the fix was prose ("pin the literal too") rather than something a machine
checks (CMX-304, orchestrator 2026-08-17: "a defect that recurs after a rule belongs in the
TOOL", not just in a comment on the one test file that happened to hit it).

This module is that tool. It statically scans every `tests/test_*.py` file for an
`assert`/`assert all(...)`-style comparison whose expected side is a bare attribute access
into a module imported via `from chela import X` (the module under test) — e.g.
`diffsurface._GIT_TIMEOUT` — where that attribute is a NUMERIC constant (the class of
constant this shape actually bites: a timeout, a byte cap, a walk bound — not a string/enum
status tag like `judge.J_CLEAN`, where the identity, not a magic number, is what a test
correctly wants to compare against) and the OTHER side of the comparison is not a literal.
If no OTHER assertion in that same file pins that exact `(module, constant)` pair against a
literal, the guard the comparison is written as can never see the constant itself drift —
only a call site that independently forgets to pass it.

Running this once, repo-wide, over every module already using this idiom (not just
diffsurface) found the same live gap in `tests/test_sessions.py` (`_MAX_ANCESTRY`),
`tests/test_sessions_proc_shim.py` (`_MAX_CHILDREN`), `tests/test_update.py`
(`GIT_NET_TIMEOUT_SECONDS`), `tests/test_dispatch_hold.py` (`MAX_TTL_SECONDS`),
`tests/test_hooks.py` (`RECAP_TIMEOUT`), and `tests/test_config_env.py`
(`DEFAULT_DASHBOARD_PORT`) — each closed alongside this guard so the check ships green, not
just added and left to fail on day one.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"

# Ops that can ever count as a "pin" against a literal (an inequality floor/ceiling is a
# real, if weaker, pin too — see agent_manager._STATUS_CMD_TIMEOUT's `>= 45.0`).
_PIN_OPS = (ast.Eq, ast.Is, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
# Ops that make a comparison "moving" (a real equality claim, not just a bound) when its
# expected side is a bare module attribute with no literal on the other side.
_MOVING_OPS = (ast.Eq, ast.Is)

_module_cache: dict[str, object] = {}


def _real_chela_module(name: str):
    if name not in _module_cache:
        _module_cache[name] = importlib.import_module(f"chela.{name}")
    return _module_cache[name]


def _module_under_test_names(tree: ast.AST) -> dict[str, str]:
    """``{local_name: real_module_name}`` for every ``from chela import X [as Y]``."""
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "chela":
            for alias in node.names:
                names[alias.asname or alias.name] = alias.name
    return names


def _is_bare_module_attr(node: ast.AST, module_names: dict[str, str]) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in module_names
    )


def _is_numeric_module_constant(real_module_name: str, attr: str) -> bool:
    try:
        mod = _real_chela_module(real_module_name)
    except ImportError:
        return False
    value = getattr(mod, attr, None)
    # bool is an int subclass but is a flag, not a bound/cap — excluded on purpose.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def find_self_referential_constant_comparisons(source: str, filename: str = "<test>"):
    """Return ``[(module_local_name, attr, lineno), ...]`` for every comparison in
    ``source`` that reads a numeric constant off a `from chela import X`-imported module on
    one side, something non-literal on the other, via `==`/`is`, with no OTHER comparison
    anywhere in the same source pinning that same ``(module, attr)`` pair against a literal.
    """
    tree = ast.parse(source, filename=filename)
    module_names = _module_under_test_names(tree)
    if not module_names:
        return []

    pinned: set[tuple[str, str]] = set()
    moving: list[tuple[str, str, int]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        op = node.ops[0]
        if not isinstance(op, _PIN_OPS):
            continue
        left, right = node.left, node.comparators[0]
        for attr_side, other_side in ((left, right), (right, left)):
            if not _is_bare_module_attr(attr_side, module_names):
                continue
            local_name = attr_side.value.id
            real_name = module_names[local_name]
            if not _is_numeric_module_constant(real_name, attr_side.attr):
                break
            if isinstance(other_side, ast.Constant):
                pinned.add((local_name, attr_side.attr))
            elif isinstance(op, _MOVING_OPS):
                moving.append((local_name, attr_side.attr, node.lineno))
            break

    return [
        (mod, attr, lineno)
        for mod, attr, lineno in moving
        if (mod, attr) not in pinned
    ]


# --- the scanner's own correctness, proven on synthetic source, independent of whatever ---
# --- tests/*.py happens to contain today -------------------------------------------------

# A real numeric constant to drive the synthetic snippets below, so
# `_is_numeric_module_constant` exercises a real import instead of a mock.
_REAL_NUMERIC_MODULE, _REAL_NUMERIC_ATTR = "diffsurface", "_GIT_TIMEOUT"


def test_scanner_flags_a_moving_comparison_with_no_literal_pin():
    src = (
        f"from chela import {_REAL_NUMERIC_MODULE}\n"
        "def test_x():\n"
        "    calls = [15, 15]\n"
        f"    assert all(t == {_REAL_NUMERIC_MODULE}.{_REAL_NUMERIC_ATTR} for t in calls)\n"
    )
    found = find_self_referential_constant_comparisons(src)
    assert found == [(_REAL_NUMERIC_MODULE, _REAL_NUMERIC_ATTR, 4)], found


def test_scanner_accepts_the_same_comparison_once_a_literal_pin_exists():
    src = (
        f"from chela import {_REAL_NUMERIC_MODULE}\n"
        "def test_x():\n"
        "    calls = [15, 15]\n"
        f"    assert all(t == {_REAL_NUMERIC_MODULE}.{_REAL_NUMERIC_ATTR} for t in calls)\n"
        f"    assert {_REAL_NUMERIC_MODULE}.{_REAL_NUMERIC_ATTR} == 15\n"
    )
    assert find_self_referential_constant_comparisons(src) == []


def test_scanner_ignores_a_string_status_constant_compared_by_identity():
    # `judge.J_CLEAN`-style sentinels are strings whose IDENTITY, not a magic number, is
    # the invariant a test correctly wants — comparing against the symbol itself is the
    # right shape there, not the defect this scanner exists to catch.
    src = (
        "from chela import judge\n"
        "def test_x():\n"
        "    result = get_verdict()\n"
        "    assert result.verdict == judge.J_CLEAN\n"
    )
    assert find_self_referential_constant_comparisons(src) == []


# --- the repo-wide sweep -------------------------------------------------------------------

def test_no_test_file_asserts_a_numeric_constant_against_its_own_symbol_unpinned():
    """Seen to go red: reverting any one of this PR's companion literal pins (e.g. dropping
    `assert sessions._MAX_ANCESTRY == 6` from tests/test_sessions.py) — the file's other
    `sessions._MAX_ANCESTRY`-derived assertions stay exactly as fragile as
    `test_all_git_subprocess_calls_are_bounded_by_git_timeout` was before CMX-299 round 10,
    and this test is what notices instead of needing a fourteenth round to find it by hand.
    """
    violations = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        found = find_self_referential_constant_comparisons(
            path.read_text(), filename=str(path)
        )
        for mod, attr, lineno in found:
            violations.append(f"{path.name}:{lineno}: `{mod}.{attr}` compared with no "
                               f"literal pin for that constant anywhere else in the file")

    assert not violations, (
        "found comparison(s) against a numeric constant re-imported from the module under "
        "test, with no companion `module.CONST == <literal>` pin anywhere in the same file "
        "— such a comparison cannot see the constant itself drift (DEFEAT_SHAPES #5); add "
        "`assert <module>.<CONST> == <literal-you-see-in-the-source-today>` next to it:\n  "
        + "\n  ".join(violations)
    )
