"""⚡⚖️ CMX-407 — which tests can OBSERVE a mutated file, so a mutation runs only those.

Every judge experiment used to re-run the whole suite (~4 min, 4,400+ tests). Eighteen of
them hit the judge's 60-minute timeout on 2026-09-30 (#554, #556). A mutation to
``chela/judge.py`` cannot be seen by ``tests/test_wall_dock.py``, so running it is time spent
proving nothing.

⛔ SELECTION ONLY EVER MAKES A RUN SMALLER — IT NEVER DECIDES A VERDICT. The contract with
:mod:`chela.judge` is asymmetric, and both halves are what make it safe:

* a RED subset is final. If the tests that import the mutated file go red, the full suite
  (which contains them) goes red too. Narrowing cannot invent a KILL.
* a GREEN subset is NOT final. The tests that SHOULD have caught the mutation may simply not
  have been selected, so the judge re-runs every subset survivor against the FULL suite
  before it may block on it. Narrowing cannot invent a SURVIVOR either.

So a selection that is too narrow costs time (one more full run), never correctness. What
selection must never do is pick tests the BASELINE did not run: a red there would be a KILL
the unmutated tree already had. The universe is therefore read back from the baseline's own
JUnit report — the exact tests ``judge.test_cmd`` ran and passed — and nothing outside it is
ever selected.

What observes a file, in the order it is checked (all of them, unioned):

1. the file itself, when it is a test file;
2. the test files that IMPORT it — Python via ``ast`` (``import a.b``, ``from a import b``,
   relative imports), JS via its ``import``/``export … from``/``import()`` specifiers, followed
   TRANSITIVELY through the JS module graph (a test imports ``app.js``, which imports the
   mutated ``util.js``);
3. any test file that NAMES it — its repo path, its basename, or (Python) its dotted module
   name, as in ``monkeypatch.setattr("chela.judge.run_suite", …)`` or a test that reads a
   CSS/JS file off disk;
4. a coverage-derived map, when ``pytest-cov`` is installed in the judged tree: one baseline
   run with ``--cov-context=test`` records which test touched which file. Cached per head sha.

Anything it cannot reason about — an unknown file type, a ``conftest.py`` (which every test
under it imports implicitly), a ``test_cmd`` that is not a plain pytest invocation, a baseline
with no readable JUnit report, a file no test observes — returns ``None``: run the FULL suite.
⛔ Never skip testing a mutation.
"""
from __future__ import annotations

import ast
import json
import re
import shlex
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

PY_SUFFIXES = (".py",)
JS_SUFFIXES = (".js", ".mjs", ".cjs")

# A shell command made of more than one command (or redirecting, or substituting) cannot be
# extended by appending pytest arguments: they would land on whatever runs LAST, not on pytest.
_SHELL_META = re.compile(r"[;&|<>`]|\$\(")

_JS_SPECIFIERS = (
    re.compile(r"""\bimport\s+[^'";]*?\bfrom\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\bexport\s+[^'";]*?\bfrom\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\bimport\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
)

_PARAM = re.compile(r"\[(.+)\]$")


# --- the command ---------------------------------------------------------------------------


def extendable(test_cmd: str) -> bool:
    """Is ``test_cmd`` ONE pytest invocation that extra node ids can be appended to?

    ``CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`` and ``"python" -m pytest -q`` are; anything
    chained (``pytest && npm test``), piped, redirected, or not pytest at all is not — and
    those run the full suite for every mutation, exactly as before CMX-407.
    """
    if not test_cmd or _SHELL_META.search(test_cmd):
        return False
    try:
        tokens = shlex.split(test_cmd)
    except ValueError:
        return False
    for i, tok in enumerate(tokens):
        name = Path(tok).name
        if name in ("pytest", "py.test"):
            return True
        if tok == "-m" and i + 1 < len(tokens) and tokens[i + 1] == "pytest":
            return True
    return False


def extend(test_cmd: str, args: list[str]) -> str:
    """``test_cmd`` with ``args`` appended, each one shell-quoted (node ids carry ``[``)."""
    return f"{test_cmd.rstrip()} {shlex.join(args)}" if args else test_cmd


# --- the universe: what the baseline actually ran ------------------------------------------


@dataclass(frozen=True)
class Case:
    """One test case the baseline ran, as its JUnit report names it."""
    file: str           # repo-relative path of the Python test file that defines it
    node: str           # its pytest node id (``file::Class::name``)
    passed: bool        # passed in the baseline (not skipped/failed/errored)
    js: str = ""        # the JS suite it runs, when its param IS a JS file path


def _case_file(root: Path, el: ET.Element) -> tuple[str, list[str]] | None:
    """(file, class parts) for one ``<testcase>``: its ``file`` attribute when present
    (xunit1), else the longest ``classname`` prefix that is a real ``.py`` file."""
    classname = el.get("classname") or ""
    parts = classname.split(".") if classname else []
    attr = el.get("file")
    if attr and (root / attr).is_file():
        rel = Path(attr).as_posix()
        stem = rel[:-3].replace("/", ".") if rel.endswith(".py") else ""
        rest = classname[len(stem):].lstrip(".") if stem and classname.startswith(stem) else ""
        return rel, rest.split(".") if rest else []
    for k in range(len(parts), 0, -1):
        rel = "/".join(parts[:k]) + ".py"
        if (root / rel).is_file():
            return rel, parts[k:]
    return None


def parse_junit(root: Path, junit_path: Path) -> list[Case] | None:
    """Every case in the baseline's JUnit report, or ``None`` if it cannot be read (⇒ the
    caller runs the full suite for every mutation — an unreadable universe selects nothing)."""
    try:
        tree = ET.parse(junit_path)
    except (OSError, ET.ParseError):
        return None
    cases: list[Case] = []
    for el in tree.iter("testcase"):
        where = _case_file(root, el)
        name = el.get("name") or ""
        if where is None or not name:
            return None     # a case we cannot place is a universe we do not fully know
        rel, klass = where
        node = "::".join([rel, *klass, name])
        passed = not any(child.tag in ("skipped", "failure", "error") for child in el)
        js = ""
        m = _PARAM.search(name)
        if m and m.group(1).endswith(JS_SUFFIXES) and (root / m.group(1)).is_file():
            js = Path(m.group(1)).as_posix()
        cases.append(Case(rel, node, passed, js))
    return cases or None


# --- the coverage map (optional) -----------------------------------------------------------


def coverage_args(data_file: Path) -> tuple[list[str], dict[str, str]]:
    """The extra pytest args + env that make ONE baseline run record, per test, which files
    it touched. ``--cov-report=`` keeps pytest-cov's report out of the suite's own output."""
    return (["--cov=.", "--cov-context=test", "--cov-report="],
            {"COVERAGE_FILE": str(data_file)})


def parse_coverage(root: Path, data_file: Path) -> dict[str, list[str]]:
    """``{source file: [test files that executed it]}`` straight out of coverage.py's SQLite
    data file — read with ``sqlite3``, so the judge's own interpreter never needs coverage.py.
    Any error returns ``{}``: the map is an optimisation, never a requirement."""
    out: dict[str, set[str]] = {}
    try:
        conn = sqlite3.connect(f"file:{data_file}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows: list[tuple[str, str]] = []
        for table in ("line_bits", "arc"):
            if table in tables:
                rows += conn.execute(
                    f"SELECT DISTINCT f.path, c.context FROM {table} t "
                    "JOIN file f ON f.id = t.file_id JOIN context c ON c.id = t.context_id"
                ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    base = root.resolve()
    for path, context in rows:
        test_file = (context or "").split("::", 1)[0]
        if not test_file or "::" not in (context or ""):
            continue            # the empty (non-test) context: imports at collection time
        try:
            rel = Path(path).resolve().relative_to(base).as_posix()
        except ValueError:
            continue
        out.setdefault(rel, set()).add(test_file)
    return {k: sorted(v) for k, v in out.items()}


def _cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "chela-judge-select"


def load_cached_coverage(sha: str) -> dict[str, list[str]] | None:
    if not sha:
        return None
    try:
        data = json.loads((_cache_dir() / f"{sha}.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_cached_coverage(sha: str, cov: dict[str, list[str]]) -> None:
    if not sha or not cov:
        return
    try:
        _cache_dir().mkdir(parents=True, exist_ok=True)
        (_cache_dir() / f"{sha}.json").write_text(json.dumps(cov))
    except OSError:
        pass


# --- static analysis -----------------------------------------------------------------------


def module_names(rel: str) -> set[str]:
    """The dotted names a Python file can be imported as: ``chela/judge.py`` →
    ``chela.judge``; ``chela/__init__.py`` → ``chela``; a helper under ``tests/`` also by its
    bare name (``tests/helpers.py`` → ``helpers``), since pytest puts ``tests/`` on the path."""
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return set()
    names = {".".join(parts)}
    if parts[0] in ("tests", "test", "src") and len(parts) > 1:
        names.add(".".join(parts[1:]))
    return names


def py_imports(rel: str, text: str) -> set[str]:
    """Every dotted name a Python file imports, at any depth (function-local imports too —
    chela imports lazily inside functions all over). ``from a import b`` yields both ``a``
    and ``a.b``, because ``b`` may be a module."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    pkg = list(Path(rel).parent.parts)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                up = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                base = ".".join([*up, *([node.module] if node.module else [])])
            else:
                base = node.module or ""
            if base:
                names.add(base)
            names.update(f"{base}.{a.name}" if base else a.name for a in node.names)
    return names


def _imports_module(imported: set[str], targets: set[str]) -> bool:
    for name in imported:
        for t in targets:
            if name == t or name.startswith(t + "."):
                return True
    return False


def js_specifiers(text: str) -> list[str]:
    out: list[str] = []
    for rx in _JS_SPECIFIERS:
        out += rx.findall(text)
    return out


def _resolve_js(root: Path, importer: str, spec: str) -> str | None:
    """A RELATIVE specifier resolved to a repo-relative file, or ``None`` (a bare package
    like ``jsdom`` is not in this repo; nothing to follow)."""
    if not spec.startswith("."):
        return None
    base = (root / importer).parent / spec
    for cand in (base, base.with_name(base.name + ".js"), base.with_name(base.name + ".mjs")):
        try:
            if cand.is_file():
                return cand.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            return None
    return None


def names_it(text: str, rel: str) -> bool:
    """Does this test's SOURCE name the file — its path, its basename as a standalone token,
    or (Python) its dotted module name? Over-matching only costs time; it is the cheap half."""
    if rel in text:
        return True
    base = re.escape(Path(rel).name)
    if re.search(rf"(?<![\w.\-]){base}(?![\w])", text):
        return True
    if rel.endswith(PY_SUFFIXES):
        for mod in module_names(rel):
            if "." in mod and re.search(rf"(?<![\w.]){re.escape(mod)}(?![\w])", text):
                return True
    return False


# --- the selector --------------------------------------------------------------------------


@dataclass
class Selection:
    """What to run for one mutation. ``nodes`` is empty only on the FULL-suite fallback."""
    nodes: list[str]
    expected: int           # baseline-passing cases the nodes cover — the subset's "baseline"
    why: str                # how they were chosen, or why the full suite runs instead

    @property
    def full(self) -> bool:
        return not self.nodes


@dataclass
class Selector:
    root: Path
    cases: list[Case]
    coverage: dict[str, list[str]] = field(default_factory=dict)
    _text: dict[str, str] = field(default_factory=dict, repr=False)
    _py_imports: dict[str, set[str]] = field(default_factory=dict, repr=False)
    _js_closure: dict[str, set[str]] = field(default_factory=dict, repr=False)

    # -- cached reads --

    def _read(self, rel: str) -> str:
        if rel not in self._text:
            try:
                self._text[rel] = (self.root / rel).read_text(errors="replace")
            except OSError:
                self._text[rel] = ""
        return self._text[rel]

    def _imports_of(self, rel: str) -> set[str]:
        if rel not in self._py_imports:
            self._py_imports[rel] = py_imports(rel, self._read(rel))
        return self._py_imports[rel]

    def _closure_of(self, rel: str) -> set[str]:
        """Every repo file a JS test reaches through relative imports, transitively."""
        if rel not in self._js_closure:
            seen: set[str] = set()
            stack = [rel]
            while stack:
                cur = stack.pop()
                for spec in js_specifiers(self._read(cur)):
                    dep = _resolve_js(self.root, cur, spec)
                    if dep and dep not in seen:
                        seen.add(dep)
                        if dep.endswith(JS_SUFFIXES):
                            stack.append(dep)
            self._js_closure[rel] = seen
        return self._js_closure[rel]

    # -- the choice --

    def _sources(self) -> tuple[set[str], set[str]]:
        py = {c.file for c in self.cases if not c.js}
        js = {c.js for c in self.cases if c.js}
        return py, js

    def observers(self, rel: str) -> set[str]:
        """Test SOURCES (Python test files and JS suites) that can observe ``rel``."""
        py_tests, js_tests = self._sources()
        found: set[str] = set()
        if rel in py_tests or rel in js_tests:
            found.add(rel)
        if rel.endswith(PY_SUFFIXES):
            targets = module_names(rel)
            found |= {t for t in py_tests if _imports_module(self._imports_of(t), targets)}
        elif rel.endswith(JS_SUFFIXES):
            found |= {t for t in js_tests if rel in self._closure_of(t)}
        found |= {t for t in py_tests | js_tests if t != rel and names_it(self._read(t), rel)}
        found |= {t for t in self.coverage.get(rel, ()) if t in py_tests}
        return found

    def select(self, rel: str) -> Selection:
        rel = Path(rel).as_posix()
        if not rel.endswith(PY_SUFFIXES + JS_SUFFIXES):
            return Selection([], 0, f"{Path(rel).suffix or 'no-suffix'} is not a file type "
                                    "selection can reason about — full suite")
        if Path(rel).name == "conftest.py":
            return Selection([], 0, "a conftest.py is imported by every test under it — "
                                    "full suite")
        try:
            sources = self.observers(rel)
        except Exception as e:      # selection is an optimisation; any failure ⇒ full suite
            return Selection([], 0, f"selection failed ({e}) — full suite")
        chosen = [c for c in self.cases
                  if (c.file in sources and not c.js) or (c.js and c.js in sources)]
        if not chosen:
            return Selection([], 0, "no test in the baseline observes it — full suite")
        # A whole Python file is one node; a JS suite is its own parametrized case.
        nodes: list[str] = []
        for c in chosen:
            node = c.file if (c.file in sources and not c.js) else c.node
            if node not in nodes:
                nodes.append(node)
        covered = [c for c in self.cases
                   if c.file in nodes or c.node in nodes]
        expected = sum(1 for c in covered if c.passed)
        return Selection(nodes, expected, f"{len(nodes)} node(s) observe {rel}")
