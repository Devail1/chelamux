"""``chela.service_code`` — which files each ``chela-*`` PM2 service runs (CMX-56).

Read against the REAL package source: the point is that the daemon's and the telegram
bridge's import closures genuinely exclude the dashboard, which a fixture tree could only
assert about itself.
"""
from __future__ import annotations

from chela import service_code


def test_daemon_and_telegram_never_run_dashboard_code():
    for svc in ("chela-daemon", "chela-telegram"):
        paths = service_code.service_paths(svc)
        assert paths is not None
        assert service_code.touches(paths, ["chela/dashboard/app.py", "chela/usage.py",
                                            "chela/dashboard/static/js/nav.js"]) == [], svc


def test_dashboard_runs_its_own_code_and_shared_core():
    paths = service_code.service_paths("chela-dashboard")
    assert paths is not None
    changed = ["chela/dashboard/app.py", "chela/usage.py", "chela/dashboard/static/js/nav.js",
               "chela/config.py", "uv.lock"]
    assert service_code.touches(paths, changed) == changed


def test_a_handler_only_reaches_its_own_lazy_imports():
    """`chela/main.py` lazily imports the dashboard inside `cmd_dashboard` only — following
    every function in main.py would put it in every service's closure."""
    assert "chela/dashboard/app.py" in service_code.service_paths("chela-dashboard")
    assert "chela/dashboard/app.py" not in service_code.service_paths("chela-daemon")
    assert "chela/collab_host.py" in service_code.service_paths("chela-collab")


def test_relative_imports_resolve_against_the_importing_package():
    names = service_code._imported_names(
        [__import__("ast").parse("from . import a\nfrom .b import c\nfrom .. import d")],
        "chela.telegram")
    assert {"chela.telegram.a", "chela.telegram.b", "chela.telegram.b.c", "chela.d"} <= names


def test_the_wall_script_service_runs_only_its_scripts():
    assert service_code.service_paths("chela-agent-terminals") == frozenset(
        {"scripts/agent-terminals.sh", "scripts/chela-env.sh"})


def test_an_unknown_service_has_no_code_set():
    assert service_code.service_paths("chela-mystery") is None
