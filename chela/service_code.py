"""Which files each ``chela-*`` PM2 service actually runs (CMX-56).

A running service is stale only when a file it LOADED changed after it started — not
whenever HEAD moves. Comparing a start time against HEAD's commit time alone flagged
``chela-daemon`` and ``chela-telegram`` as stale after a dashboard-only commit (neither
imports a single dashboard module), and that false alarm is what the Settings → Update
card showed. CLAUDE.md's deploy rule already says it: restart by import graph.

:func:`service_paths` answers "which repo paths does service X run", as a set of
repo-relative paths plus directory prefixes (ending in ``/``), computed statically:

* a CLI service (``chela <subcommand>``, started through ``scripts/run-chela.sh``) runs
  ``chela/main.py``'s module-level imports, ``main()``'s own imports, and whatever its
  ``cmd_*`` handler reaches — the handler's imports plus those of every top-level
  ``main.py`` function it references, transitively. Every other module is followed
  through ALL of its imports, function-level included (a lazy import still executes in
  the long-running process; over-including only ever costs an extra restart).
* plus the files that shape every CLI process (the launcher scripts, ``pyproject.toml``,
  ``uv.lock``) and any non-Python files a service reads (its package directory).
* ``chela-agent-terminals`` is a bash loop: its own script is the only code it holds
  for its lifetime (the ``python -c`` snippets it runs start a fresh interpreter each poll).

A service this module does not know returns ``None`` — the caller reports it UNKNOWN,
never fresh and never stale.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

# The package source this process imported — the same tree as the checkout's HEAD in
# every real deploy (`update.repo_root()` is derived from the same `__file__`).
SOURCE_ROOT = Path(__file__).resolve().parent.parent

# PM2 name -> the `chela/main.py` subcommand handler it runs (examples/ecosystem.config.js).
CLI_SERVICES = {
    "chela-daemon": "cmd_run",
    "chela-dashboard": "cmd_dashboard",
    "chela-telegram": "cmd_telegram",
    "chela-collab": "cmd_collab",
}

# Files that shape EVERY `chela <subcommand>` process without being imported by it.
CLI_RUNTIME_PATHS = (
    "scripts/run-chela.sh", "scripts/chela-env.sh", "pyproject.toml", "uv.lock",
)

# Non-Python files a service reads (templates, static assets, manifests) — whole package.
SERVICE_DATA_PREFIXES = {
    "chela-dashboard": ("chela/dashboard/",),
    "chela-telegram": ("chela/telegram/",),
}

SCRIPT_SERVICES = {
    "chela-agent-terminals": ("scripts/agent-terminals.sh", "scripts/chela-env.sh"),
}


def _module_path(root: Path, dotted: str) -> str | None:
    """``chela.x.y`` -> ``chela/x/y.py`` (or its package ``__init__.py``), repo-relative."""
    parts = dotted.split(".")
    if parts[0] != "chela":
        return None
    for rel in ("/".join(parts) + ".py", "/".join(parts) + "/__init__.py"):
        if (root / rel).is_file():
            return rel
    return None


def _package_of(rel: str) -> str:
    """The dotted package a module's relative imports resolve against."""
    parts = rel[:-3].split("/")
    return ".".join(parts[:-1])  # `chela/x/__init__.py` -> `chela.x`; `chela/x.py` -> `chela`


def _imported_names(nodes, package: str) -> set[str]:
    """Every dotted module name an ``import`` under ``nodes`` may load (candidates —
    ``from a import b`` names both ``a`` and ``a.b``; non-modules simply won't resolve)."""
    names: set[str] = set()
    for node in nodes:
        for n in ast.walk(node):
            if isinstance(n, ast.Import):
                names.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                if n.level:
                    base = package.split(".")[: len(package.split(".")) - (n.level - 1)]
                    mod = ".".join(base + ([n.module] if n.module else []))
                else:
                    mod = n.module or ""
                names.add(mod)
                names.update(f"{mod}.{a.name}" for a in n.names)
    return names


def _parse(root: Path, rel: str) -> ast.Module | None:
    try:
        return ast.parse((root / rel).read_text())
    except (OSError, SyntaxError, ValueError):
        return None


def _closure(root: Path, seeds: set[str]) -> set[str] | None:
    """Repo-relative paths of every ``chela`` module reachable from ``seeds`` (dotted
    names), each followed through all of its imports. ``None`` if one can't be parsed."""
    seen: set[str] = set()
    todo = list(seeds)
    while todo:
        dotted = todo.pop()
        # Importing `chela.a.b` runs `chela/__init__.py` and `chela/a/__init__.py` first.
        parts = dotted.split(".")
        for i in range(1, len(parts)):
            todo.append(".".join(parts[:i]))
        rel = _module_path(root, dotted)
        if rel is None or rel in seen:
            continue
        seen.add(rel)
        tree = _parse(root, rel)
        if tree is None:
            return None
        todo.extend(_imported_names([tree], _package_of(rel)))
    return seen


def _main_seeds(tree: ast.Module, command: str) -> set[str] | None:
    """What ``chela <command>`` imports out of ``chela/main.py``: module-level imports,
    ``main()``'s own imports (it builds the parser for every subcommand), and the
    handler's — plus every top-level function the handler references, transitively.
    The OTHER handlers' lazy imports are exactly what this must leave out."""
    funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if command not in funcs or "main" not in funcs:
        return None
    module_level = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    seeds = _imported_names(module_level, "chela") | _imported_names([funcs["main"]], "chela")
    reached: set[str] = set()
    todo = [command]
    while todo:
        name = todo.pop()
        if name in reached:
            continue
        reached.add(name)
        seeds |= _imported_names([funcs[name]], "chela")
        todo.extend(n.id for n in ast.walk(funcs[name])
                    if isinstance(n, ast.Name) and n.id in funcs and n.id != "main")
    return seeds


@functools.lru_cache(maxsize=64)
def _cli_paths(root: Path, command: str, _key: tuple) -> frozenset[str] | None:
    tree = _parse(root, "chela/main.py")
    if tree is None:
        return None
    seeds = _main_seeds(tree, command)
    if seeds is None:
        return None
    mods = _closure(root, seeds)
    if mods is None:
        return None
    return frozenset(mods | {"chela/main.py"})


def _tree_key(root: Path) -> tuple:
    """Cache key that moves whenever any module file does (a pull, an edit)."""
    try:
        return tuple(sorted((str(p), p.stat().st_mtime_ns) for p in (root / "chela").rglob("*.py")))
    except OSError:
        return (object(),)  # uncacheable: never equal to another key


def service_paths(service: str, root: Path | None = None) -> frozenset[str] | None:
    """Repo paths (and ``dir/`` prefixes) whose change makes a running ``service`` stale.
    ``None`` when this can't be told — an unknown service, or source that won't parse."""
    root = root or SOURCE_ROOT
    if service in SCRIPT_SERVICES:
        return frozenset(SCRIPT_SERVICES[service])
    command = CLI_SERVICES.get(service)
    if command is None:
        return None
    mods = _cli_paths(root, command, _tree_key(root))
    if mods is None:
        return None
    extra: tuple[str, ...] = SERVICE_DATA_PREFIXES.get(service, ())
    if service == "chela-collab":
        from chela import collab_host
        extra += collab_host.COLLAB_HOST_PATHS
    return mods | frozenset(CLI_RUNTIME_PATHS) | frozenset(extra)


def touches(paths: frozenset[str], changed: list[str]) -> list[str]:
    """The ``changed`` repo paths that fall inside ``paths`` (exact, or under a prefix)."""
    prefixes = tuple(p for p in paths if p.endswith("/"))
    return [c for c in changed if c in paths or (prefixes and c.startswith(prefixes))]
