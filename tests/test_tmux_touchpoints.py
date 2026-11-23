"""CMX-474: every place chela types into or reads a tmux pane is listed in docs/TMUX_TOUCHPOINTS.md.

The doc is an audit, and an audit is only worth something while it is complete. This test
walks ``chela/`` with :mod:`ast` and fails on any pane-touching call site the doc does not
have a row for, so a new ``send-keys`` / ``capture-pane`` cannot land unlisted. It also
fails on a row that names a function which no longer exists, so a rename or a removal
cannot leave the doc describing code that is gone, and on a row with no bucket or no
recommendation.

A *site* is the innermost enclosing function (``Class.method``, ``outer.inner``) of:

* a tmux argv literal ``["tmux", <verb>, ...]`` anywhere in it, built inline or kept in a
  variable or returned (``dashboard/app.py:_term_keyargv`` returns one);
* a call that passes ``<verb>`` as a bare positional argument (``_tmux("send-keys", ...)``);
* a same-module call, by bare name, to a module-level PRIVATE function that is itself a
  site (one hop: ``dispatcher.tick`` calling ``_capture_pane``);
* a call to, or a reference to, one of :mod:`chela.messenger`'s pane primitives, as
  ``messenger.<name>`` or as a bare name imported from ``chela.messenger``
  (``capture = capture or messenger.capture_pane`` wires a reader just as surely as a call).

``<verb>`` is ``send-keys``, ``capture-pane`` or ``paste-buffer`` (the multi-line half of
typing into a pane). Comments and docstrings never count: only code is parsed.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "TMUX_TOUCHPOINTS.md"

VERBS = frozenset({"send-keys", "capture-pane", "paste-buffer"})
# messenger's pane primitives: each one types into or reads a pane on its caller's behalf.
PRIMITIVES = frozenset({
    "send_tmux", "resend_enter", "send_key", "send_escape", "capture_pane", "_capture_pane",
    "deliver_prompt", "deliver_image", "refuses_paste", "_read_draft",
})
BUCKETS = frozenset({"1", "2", "3", "4", "5", "6"})
RECOMMENDATIONS = frozenset({"keep", "replace", "remove"})
_SHELL_VERB = re.compile(r"\btmux\b.*\b(send-keys|capture-pane|paste-buffer)\b")


def _is_tmux_argv(node: ast.AST) -> bool:
    """``["tmux", …, <verb>, …]``: the verb anywhere after ``tmux``, so a socket prefix
    (``-L sock``) or a spread of global flags in front of it cannot hide the site."""
    if not isinstance(node, (ast.List, ast.Tuple)) or len(node.elts) < 2:
        return False
    head = node.elts[0]
    return (isinstance(head, ast.Constant) and head.value == "tmux"
            and any(isinstance(e, ast.Constant) and e.value in VERBS for e in node.elts[1:]))


def _is_messenger_module(node: ast.ImportFrom) -> bool:
    """``from chela.messenger import …`` or the relative ``from .messenger import …``."""
    return node.module == "chela.messenger" or (node.level > 0 and node.module == "messenger")


def _messenger_aliases(tree: ast.AST) -> frozenset[str]:
    """Names the ``chela.messenger`` MODULE is bound to here (``messenger`` by default)."""
    names = {"messenger"}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (n.module == "chela" or (n.level > 0 and not n.module)):
            names |= {a.asname or a.name for a in n.names if a.name == "messenger"}
        elif isinstance(n, ast.Import):
            names |= {a.asname for a in n.names if a.name == "chela.messenger" and a.asname}
    return frozenset(names)


def _messenger_names(tree: ast.AST, rel: str) -> frozenset[str]:
    """The primitives callable by BARE name in this module: imported from chela.messenger
    (at module or function level), or all of them inside messenger itself. A bare name
    that is not one of these is somebody else's variable (``e2e.py`` has a ``send_key``
    that is a crypto key), never a pane write."""
    if rel == "chela/messenger.py":
        return PRIMITIVES
    return frozenset(
        a.asname or a.name
        for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and _is_messenger_module(n)
        for a in n.names if a.name in PRIMITIVES)


def _touches(node: ast.AST, bare: frozenset[str], modules: frozenset[str]) -> bool:
    """Does this single node (not its children) type into or read a pane?"""
    if _is_tmux_argv(node):
        return True
    if isinstance(node, ast.Call) and any(
            isinstance(a, ast.Constant) and a.value in VERBS for a in node.args):
        return True
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return node.id in bare
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        return (node.attr in PRIMITIVES and isinstance(node.value, ast.Name)
                and node.value.id in modules)
    return False


def python_sites(source: str, rel: str) -> set[str]:
    """``{"<rel>:<qualname>"}`` for every pane-touching site in one module's ``source``."""
    found: set[str] = set()
    tree = ast.parse(source)
    bare = _messenger_names(tree, rel)
    modules = _messenger_aliases(tree)

    def walk(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, [*stack, child.name])
                continue
            if _touches(child, bare, modules):
                found.add(f"{rel}:{'.'.join(stack) or '<module>'}")
            walk(child, stack)

    walk(tree, [])
    # One hop through a module's own PRIVATE wrapper: a function calling `_capture_pane(…)`
    # or `_send(…)` by bare name in the same module touches the pane just as directly.
    wrappers = {site.split(":", 1)[1] for site in found}
    wrappers = {w for w in wrappers if "." not in w and w.startswith("_")}

    def hop(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                hop(child, [*stack, child.name])
                continue
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                    and child.func.id in wrappers and stack and stack[-1] != child.func.id):
                found.add(f"{rel}:{'.'.join(stack)}")
            hop(child, stack)

    hop(tree, [])
    return found


def shell_sites(text: str, rel: str) -> set[str]:
    """``{rel}`` when a non-comment line of a shell script runs a tmux pane verb."""
    for line in text.splitlines():
        code = line.split("#", 1)[0]
        if _SHELL_VERB.search(code):
            return {rel}
    return set()


def code_sites(root: Path = ROOT) -> set[str]:
    sites: set[str] = set()
    for path in sorted((root / "chela").rglob("*.py")):
        sites |= python_sites(path.read_text(), path.relative_to(root).as_posix())
    for path in sorted([*(root / "scripts").rglob("*.sh"), *(root / "plugin").rglob("*.sh")]):
        sites |= shell_sites(path.read_text(), path.relative_to(root).as_posix())
    return sites


_ROW = re.compile(r"^\|\s*`(?P<site>[\w./-]+(?::[\w.<>]+)?)`\s*\|(?P<rest>.*)\|\s*$")


def doc_rows(text: str) -> dict[str, list[str]]:
    """``{site: [cell, ...]}`` for every table row whose first cell is a backticked site."""
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = _ROW.match(line)
        if m:
            rows[m["site"]] = [c.strip() for c in m["rest"].split("|")]
    return rows


def _resolves(site: str, root: Path = ROOT) -> bool:
    """Does the file exist and, for ``path:qualname``, define that (nested) function?"""
    rel, _, qual = site.partition(":")
    path = root / rel
    if not path.is_file():
        return False
    if not qual or qual == "<module>":
        return True
    node: ast.AST = ast.parse(path.read_text())
    for part in qual.split("."):
        # Direct children only: a row for ``outer.inner`` must go stale when ``inner``
        # moves out of ``outer``, even if a same-named function exists elsewhere.
        nxt = next((c for c in getattr(node, "body", [])
                    if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and c.name == part), None)
        if nxt is None:
            return False
        node = nxt
    return True


# --- the audit ------------------------------------------------------------------------


def test_every_code_site_has_a_row():
    missing = code_sites() - set(doc_rows(DOC.read_text()))
    assert not missing, (
        "pane-touching call sites missing from docs/TMUX_TOUCHPOINTS.md — add a row "
        f"(bucket + recommendation) for each: {sorted(missing)}")


def test_every_row_names_code_that_exists():
    stale = sorted(s for s in doc_rows(DOC.read_text()) if not _resolves(s))
    assert not stale, f"rows naming a file or function that no longer exists: {stale}"


def bucket_one_pane_sites(rows: dict[str, list[str]], sites: set[str]) -> list[str]:
    """Bucket-1 rows (a message to an agent) whose function still touches a pane."""
    return sorted(site for site, cells in rows.items()
                  if len(cells) >= 2 and cells[-2].strip("* ") == "1" and site in sites)


def test_no_message_to_an_agent_touches_a_pane():
    """CMX-482 (slice 1's acceptance): ``send_tmux`` — or any pane primitive — is reachable
    from no bucket-1 row. A message to an agent goes over the peer socket or not at all."""
    rows = doc_rows(DOC.read_text())
    assert sum(1 for c in rows.values() if c[-2].strip("* ") == "1") >= 6
    bad = bucket_one_pane_sites(rows, code_sites())
    assert not bad, f"bucket-1 (agent message) rows that still type into or read a pane: {bad}"


def test_bucket_one_pane_sites_fixture():
    rows = {"chela/a.py:f": ["x", "y", "1", "remove"], "chela/a.py:g": ["x", "y", "4", "keep"],
            "chela/a.py:h": ["x", "y", "1", "remove"]}
    assert bucket_one_pane_sites(rows, {"chela/a.py:f", "chela/a.py:g"}) == ["chela/a.py:f"]


def row_problems(rows: dict[str, list[str]]) -> dict[str, tuple[str, str]]:
    """Rows whose last two cells are not a bucket 1-6 and a keep/replace/remove."""
    bad = {}
    for site, cells in rows.items():
        bucket = cells[-2].strip("* ") if len(cells) >= 2 else ""
        rec = cells[-1].strip("* ").lower() if cells else ""
        if bucket not in BUCKETS or rec not in RECOMMENDATIONS:
            bad[site] = (bucket, rec)
    return bad


_HEADING = re.compile(r"^#{2,4} .*\(bucket (?P<n>[1-6])\)\s*$")


def heading_mismatches(text: str) -> dict[str, tuple[str, str]]:
    """Rows under a ``### … (bucket N)`` heading whose own bucket cell is not N."""
    bad: dict[str, tuple[str, str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("#"):
            m = _HEADING.match(line)
            current = m["n"] if m else None
            continue
        row = _ROW.match(line)
        if row and current:
            cells = [c.strip() for c in row["rest"].split("|")]
            bucket = cells[-2].strip("* ") if len(cells) >= 2 else ""
            if bucket != current:
                bad[row["site"]] = (current, bucket)
    return bad


def test_rows_sit_under_their_buckets_heading():
    bad = heading_mismatches(DOC.read_text())
    assert not bad, f"rows whose bucket differs from their section's (heading, cell): {bad}"


def test_heading_mismatches_fixture():
    text = ("### Reads (bucket 3)\n| `chela/a.py:f` | x | y | 3 | keep |\n"
            "| `chela/a.py:g` | x | y | 4 | keep |\n"
            "### Not sites\n| `chela/a.py:h` | x | y | 1 | keep |\n"
            "## Keys (bucket 4)\n| `chela/a.py:k` | x | y | 4 | keep |\n")
    assert heading_mismatches(text) == {"chela/a.py:g": ("3", "4")}


def test_every_row_has_a_bucket_and_a_recommendation():
    rows = doc_rows(DOC.read_text())
    assert len(rows) >= 40, "the doc's tables were not parsed"
    bad = row_problems(rows)
    assert not bad, f"rows without a bucket 1-6 and a keep/replace/remove: {bad}"


# --- the scanner itself: positive and negative fixtures -------------------------------


@pytest.mark.parametrize("src, expected", [
    ('def f():\n    subprocess.run(["tmux", "send-keys", "-t", t, "Enter"])\n', {"m.py:f"}),
    ('def f():\n    cmd = ["tmux", "capture-pane", "-p"]\n    return cmd\n', {"m.py:f"}),
    ('def f():\n    return ("tmux", "paste-buffer", "-p")\n', {"m.py:f"}),
    ('def f():\n    _tmux("send-keys", "-t", w)\n', {"m.py:f"}),
    ('def f():\n    run(["tmux", "-L", sock, "send-keys", "-t", w])\n', {"m.py:f"}),
    ('def f():\n    run(["tmux", *flags, "capture-pane", "-p"])\n', {"m.py:f"}),
    ('from chela import messenger as m\ndef f():\n    m.send_tmux(w, "x")\n', {"m.py:f"}),
    ('from . import messenger as msg\ndef f():\n    msg.capture_pane(w)\n', {"m.py:f"}),
    ('import chela.messenger as cm\ndef f():\n    cm.send_key(w, "Up")\n', {"m.py:f"}),
    ('from .messenger import send_tmux\ndef f():\n    send_tmux(w, "x")\n', {"m.py:f"}),
    ('def f():\n    messenger.send_tmux(w, "x")\n', {"m.py:f"}),
    ('from chela.messenger import send_tmux\ndef f():\n    send_tmux(w, "x")\n', {"m.py:f"}),
    ('def f():\n    from chela.messenger import capture_pane as cp\n    cp(w)\n', {"m.py:f"}),
    ('def f(c=None):\n    c = c or messenger.capture_pane\n', {"m.py:f"}),
    ('class C:\n    def m(self):\n        messenger.send_escape(w)\n', {"m.py:C.m"}),
    ('def outer():\n    def inner():\n        messenger.send_key(w, "Up")\n', {"m.py:outer.inner"}),
    ('def outer():\n    messenger.send_key(w, "Up")\n    def inner():\n        pass\n',
     {"m.py:outer"}),
    ('x = messenger.capture_pane\n', {"m.py:<module>"}),
    ('def _cap():\n    run(["tmux", "capture-pane"])\ndef tick():\n    _cap()\n',
     {"m.py:_cap", "m.py:tick"}),                                       # one hop, private
    ('def _cap():\n    run(["tmux", "capture-pane"])\nclass W:\n    def poll(self):\n'
     '        _cap()\n', {"m.py:_cap", "m.py:W.poll"}),
])
def test_scanner_finds(src, expected):
    assert python_sites(src, "m.py") == expected


@pytest.mark.parametrize("src", [
    'def f():\n    """send-keys into tmux capture-pane"""\n',          # docstring only
    'def f():\n    # tmux send-keys\n    pass\n',                       # comment only
    'def f():\n    subprocess.run(["tmux", "display-message", "-p"])\n',  # another verb
    'def f():\n    subprocess.run(["git", "send-keys"])\n',              # not tmux
    'def f():\n    x = ["send-keys", "tmux"]\n',                         # wrong order
    'def f():\n    x = ["git", "-C", d, "send-keys"]\n',                 # not a tmux argv
    'from chela import config as m\ndef f():\n    m.send_tmux(w)\n',     # not messenger
    'from .other import send_tmux\ndef f():\n    send_tmux(w)\n',        # another module
    'SPAWNS = ("new-window", "send-keys")\n',                            # a policy tuple
    'def f():\n    send_peer(w, "a", "b")\n',                            # native, not a pane
    'def f(self):\n    self._capture(w)\n',                              # injected, not a primitive
    'def f(self):\n    self.send_key(w)\n',                              # self-attr, not messenger
    'def f():\n    send_key, recv = a, b\n    AESGCM(send_key)\n',        # a local, not imported
    'from x import send_tmux\ndef f():\n    send_tmux(w)\n',            # not chela.messenger
    'def send_tmux(w, t):\n    return True\n',                           # a definition is no call
    'def run_it():\n    _cap()\n',                                       # _cap is not a site here
])
def test_scanner_ignores(src):
    assert python_sites(src, "m.py") == set()


def test_scanner_hops_only_private_wrappers_once():
    src = ('def cap():\n    run(["tmux", "capture-pane"])\ndef a():\n    cap()\n'
           'def _w():\n    run(["tmux", "send-keys"])\ndef b():\n    _w()\ndef c():\n    b()\n'
           'def _r():\n    run(["tmux", "send-keys"])\n    _r()\n')
    assert python_sites(src, "m.py") == {"m.py:cap", "m.py:_w", "m.py:b", "m.py:_r"}



@pytest.mark.parametrize("text, hit", [
    ("tmux send-keys -t x Enter\n", True),
    ("  out=$(tmux capture-pane -p -t x)\n", True),
    ("# tmux capture-pane shows the glyph\n", False),
    ("echo ok  # tmux send-keys later\n", False),
    ("tmux attach-session -t x\n", False),
    ("send-keys without the tmux word\n", False),
])
def test_shell_scanner(text, hit):
    assert shell_sites(text, "s.sh") == ({"s.sh"} if hit else set())


def test_doc_rows_parse_bucket_and_recommendation():
    text = ("| site | what | arrives as | bucket | rec |\n"
            "|---|---|---|---|---|\n"
            "| `chela/a.py:f` | types x | user turn | **1** | replace |\n"
            "| not a site | x | y | 2 | keep |\n")
    assert doc_rows(text) == {"chela/a.py:f": ["types x", "user turn", "**1**", "replace"]}


@pytest.mark.parametrize("cells, ok", [
    (["what", "user turn", "1", "replace"], True),
    (["what", "read", "**6**", "Keep"], True),
    (["what", "read", "7", "keep"], False),          # no bucket 7
    (["what", "read", "", "keep"], False),           # no bucket
    (["what", "read", "3", "maybe"], False),         # not a recommendation
    (["what", "read", "3", ""], False),              # no recommendation
    (["keep"], False),                               # too few cells
])
def test_row_problems(cells, ok):
    assert (row_problems({"chela/a.py:f": cells}) == {}) is ok


def test_resolves_rejects_a_missing_function(tmp_path):
    (tmp_path / "chela").mkdir()
    (tmp_path / "chela" / "a.py").write_text("class C:\n    def m(self):\n        pass\n")
    assert _resolves("chela/a.py:C.m", tmp_path)
    assert _resolves("chela/a.py", tmp_path)
    assert not _resolves("chela/a.py:C.gone", tmp_path)
    assert not _resolves("chela/a.py:gone", tmp_path)
    assert not _resolves("chela/b.py:f", tmp_path)
    # A nested function resolves only under the parent it is nested in.
    (tmp_path / "chela" / "n.py").write_text(
        "def outer():\n    def inner():\n        pass\ndef other():\n    def moved():\n        pass\n")
    assert _resolves("chela/n.py:outer.inner", tmp_path)
    assert not _resolves("chela/n.py:outer.moved", tmp_path)
    assert not _resolves("chela/n.py:inner", tmp_path)
