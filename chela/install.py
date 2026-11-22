"""How chela was installed, and what "update" means for that kind of install (CMX-471).

``chela update``, the dashboard's Update control and the daemon's hourly update check were
all written for a ``git clone``: fetch, pull, ``uv sync``, ``pm2 restart``. A PyPI install has
no checkout, so :func:`chela.update.repo_root` raises ``NotAGitCheckout`` and the daemon's
check logged a traceback every hour. This module answers the two questions a non-git install
needs instead:

* :func:`kind` — ``git`` (a checkout), ``uv-tool`` (``uv tool install chelamux``), ``uvx``
  (an ephemeral ``uvx chelamux`` environment) or ``pip`` (anything else: pip into a venv).
  Detected once per process.
* :func:`package_status` — the installed version against PyPI's latest. The PyPI answer is
  cached in a small file under ``CHELA_DIR``. Only the daemon's hourly check and a human's
  ``chela update`` ever ask PyPI (``fetch=True``, refreshing a cache older than
  :data:`CACHE_TTL_SECONDS`, or :data:`FAILED_TTL_SECONDS` after a failure). The dashboard's
  polls, the capability row and ``chela doctor`` read the cache only (``fetch=False``) — a
  request path never waits on the network, the same trade ``commits_behind(fetch=False)``
  makes for a checkout.

Each kind has its own upgrade path (:func:`advice`); none of them is "Update & restart",
because chela cannot upgrade a package it is running from.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from chela import __version__, config

log = logging.getLogger(__name__)

GIT = "git"
UV_TOOL = "uv-tool"
UVX = "uvx"
PIP = "pip"

PACKAGE = "chelamux"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
PYPI_TIMEOUT_SECONDS = 5
UV_TIMEOUT_SECONDS = 10
CACHE_TTL_SECONDS = 3600
FAILED_TTL_SECONDS = 600

# uv runs a `uvx` tool from an environment inside its cache: `<cache>/archive-v0/<hash>/`
# (older uv: `builds-v0`). A `uv tool install` lives under `uv tool dir` instead.
_UVX_DIR = re.compile(r"^(?:archive|builds)-v\d+$")
# A `uv tool list` line naming the package: "chelamux v0.19.1" (extras/paths may follow).
_UV_TOOL_LINE = re.compile(rf"^{PACKAGE} v\S+")
_RELEASE = re.compile(r"^v?(\d+(?:\.\d+)*)")


def _package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _uv_tool_lists_chela(run=subprocess.run) -> bool:
    """Does ``uv tool list`` show chelamux? False when uv is missing, hangs or fails."""
    try:
        cp = run(["uv", "tool", "list"], capture_output=True, text=True,
                 timeout=UV_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as e:
        log.info("install kind: `uv tool list` did not run: %s", e)
        return False
    if cp.returncode != 0:
        return False
    return any(_UV_TOOL_LINE.match(line) for line in cp.stdout.splitlines())


def _receipt_names_chela(env: Path) -> bool:
    """Is ``env`` a ``uv tool`` environment for chelamux? uv writes ``uv-receipt.toml`` into
    each tool's venv, naming the requirement it was installed for."""
    try:
        receipt = tomllib.loads((env / "uv-receipt.toml").read_text())
    except (OSError, ValueError):
        return False
    reqs = (receipt.get("tool") or {}).get("requirements") or []
    return any(isinstance(r, dict) and r.get("name") == PACKAGE for r in reqs)


def detect(package_root: Path | None = None, prefix: str | None = None,
           run=subprocess.run) -> str:
    """The install kind, from scratch. Every input is injectable for the tests.

    Classifies the RUNNING interpreter, never just the host: a pip venv on a host that ALSO
    has a ``uv tool install chelamux`` is ``pip``, since ``uv tool upgrade`` would upgrade a
    different copy. Order matters: a checkout is a checkout however its venv was made; a uvx
    environment is ephemeral; a ``uv tool`` environment is known from its receipt, or — for
    a uv that writes none — from a venv named ``chelamux`` that ``uv tool list`` shows;
    anything left is a pip-style venv install.
    """
    root = package_root if package_root is not None else _package_root()
    if (root / ".git").exists():
        return GIT
    env = Path(prefix if prefix is not None else sys.prefix)
    if any(_UVX_DIR.match(part) for part in env.parts):
        return UVX
    if _receipt_names_chela(env):
        return UV_TOOL
    if env.name == PACKAGE and _uv_tool_lists_chela(run):
        return UV_TOOL
    return PIP


@lru_cache(maxsize=1)
def kind() -> str:
    """:func:`detect`, once per process: the install does not change under a running one."""
    return detect()


def advice(install: str) -> str:
    """How to upgrade an install of this kind (git: ``chela update``)."""
    if install == GIT:
        return "run `chela update`"
    if install == UV_TOOL:
        return f"run `uv tool upgrade {PACKAGE}`, then restart the services"
    if install == UVX:
        return "uvx always runs the latest; restart the services"
    return f"run `pip install --upgrade {PACKAGE}` in chela's venv, then restart the services"


def command(install: str) -> str:
    """The one shell command that upgrades this kind, or "" when there is none (uvx)."""
    return {GIT: "chela update", UV_TOOL: f"uv tool upgrade {PACKAGE}",
            PIP: f"pip install --upgrade {PACKAGE}"}.get(install, "")


def _release(v: str) -> tuple[int, ...] | None:
    m = _RELEASE.match(v.strip())
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(latest: str, installed: str) -> bool | None:
    """Is ``latest`` a newer release than ``installed``? ``None`` when either is unreadable
    (unknown is never "up to date"). Compares release numbers only: ``0.20.0`` vs ``0.19.1``.
    """
    a, b = _release(latest), _release(installed)
    if a is None or b is None:
        return None
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _cache_path() -> Path:
    return config.CHELA_DIR / "pypi-latest.json"


def _fetch_latest() -> str:
    """PyPI's latest released version of chelamux. Raises on any failure."""
    with urllib.request.urlopen(PYPI_URL, timeout=PYPI_TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    latest = (data.get("info") or {}).get("version")
    if not isinstance(latest, str) or not latest.strip():
        raise ValueError("PyPI's answer names no version")
    return latest.strip()


def latest_version(*, fetch: bool = False, now: float | None = None) -> tuple[str, str]:
    """``(latest, error)`` — exactly one is non-empty.

    ``fetch=False`` (every request path: dashboard, capability row, doctor) reads the cache
    file only, at any age, and never touches the network; no cache yet is an error, never a
    version. ``fetch=True`` (the daemon's hourly check, ``chela update``) uses the cache while
    it is fresh, otherwise asks PyPI and caches the answer, a failure included (for the
    shorter :data:`FAILED_TTL_SECONDS`), so an offline host does not retry every tick.
    """
    now = time.time() if now is None else now
    path = _cache_path()
    try:
        cached = json.loads(path.read_text())
        age = now - float(cached["checked_at"])
        ttl = CACHE_TTL_SECONDS if cached.get("latest") else FAILED_TTL_SECONDS
        if not fetch or 0 <= age < ttl:
            return cached.get("latest") or "", cached.get("error") or ""
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        if not fetch:
            return "", "PyPI not checked yet (the daemon checks hourly; `chela update --check` checks now)"
    try:
        latest, error = _fetch_latest(), ""
    except Exception as e:  # noqa: BLE001 — any failure is "couldn't tell", never a crash
        latest, error = "", f"could not read PyPI: {e}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # atomic: the dashboard and doctor read this while the daemon writes it
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"checked_at": now, "latest": latest, "error": error}))
        os.replace(tmp, path)
    except OSError as e:
        log.info("install: could not cache PyPI's answer: %s", e)
    return latest, error


@dataclass(frozen=True)
class PackageStatus:
    """A non-git install against PyPI. ``behind`` is ``None`` when unknown."""

    install: str
    version: str
    latest: str = ""
    behind: bool | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.behind is not None

    def as_dict(self) -> dict:
        return {"ok": self.ok, "git": False, "install": self.install, "version": self.version,
                "latest": self.latest or None, "behind": self.behind,
                "command": command(self.install), "advice": advice(self.install),
                **({"error": self.error} if self.error else {})}


def package_status(install: str | None = None, *, fetch: bool = False,
                   installed: str = __version__, now: float | None = None) -> PackageStatus:
    """The installed version against PyPI's latest — see :func:`latest_version` for
    ``fetch`` (default: the cache only, never the network)."""
    install = install or kind()
    latest, error = latest_version(fetch=fetch, now=now)
    if not latest:
        return PackageStatus(install, installed, error=error or "PyPI's latest is unknown")
    behind = is_newer(latest, installed)
    if behind is None:
        return PackageStatus(install, installed, latest,
                             error=f"cannot compare {installed!r} with {latest!r}")
    return PackageStatus(install, installed, latest, behind)


def describe(st: PackageStatus) -> str:
    """One line for a log, ``chela doctor`` or ``chela update --check``."""
    head = f"{st.install} install, {PACKAGE} {st.version}"
    if not st.ok:
        return f"{head}; newer version unknown ({st.error})"
    if st.behind:
        return f"{head}; {st.latest} is on PyPI — {advice(st.install)}"
    return f"{head}; up to date with PyPI ({st.latest})"
