"""CMX-402 — the README, the landing page and their demo media are PUBLIC.

Three guards:

* the README and landing pages must not describe a dashboard view that no longer
  exists (the nav is Wall + Work; Feed, Knowledge, Personas and Cost were removed);
* nothing under ``docs/img/`` or ``landing/`` may carry the operator's user name, a
  private window name, or a ``/home/`` path — checked on the raw BYTES of every file,
  so a path baked into a GIF comment or MP4 metadata is caught as well as one in HTML;
* the demo recorder (``scripts/demo/fleet.py``) must build its fleet on its own tmux
  server (``tmux -L``) with its own ``HOME`` + ``CHELA_DIR`` under a fixed demo root
  (never ``$TMPDIR``) and a from-scratch env — the property that keeps the operator's
  real fleet out of the recordings.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_PAGES = [ROOT / "README.md", ROOT / "landing" / "index.html", ROOT / "landing" / "docs.html"]

# Case-sensitive on purpose: these are view NAMES. "zero-knowledge relay" and
# "feed" as a verb are ordinary words, not a nav view.
REMOVED_VIEWS = ["Feed", "Knowledge", "Personas", "Cost"]

PRIVATE_NEEDLES = [b"liavedunix", b"tradeplan", b"/home/"]

# Every key demo_env() may emit. Adding one is a deliberate, reviewed change here.
DEMO_ENV_KEYS = {
    "PATH", "HOME", "LANG", "TERM", "CHELA_DIR", "CHELA_TMUX_SESSION",
    "CHELA_DASHBOARD_PORT", "CHELA_DASH_HOST", "CHELA_TERM_BASE", "CHELA_TERM_POLL",
    "CHELA_TERMINALS_ENABLED", "CHELA_REMOTE_CONTROL", "CHELA_DISPATCH_WORKFLOWS",
    "CHELA_DEMO_STATUS_DIR", "PYTHON", "PYTHONPATH", "GIT_CONFIG_NOSYSTEM",
    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
}


def _text(path: Path) -> str:
    """The page's prose: HTML tags and comments dropped, so an attribute or a
    comment cannot hide or fake a match."""
    raw = path.read_text(encoding="utf-8")
    raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
    return re.sub(r"<[^>]+>", " ", raw)


@pytest.mark.parametrize("page", PUBLIC_PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_public_pages_name_no_removed_view(page):
    text = _text(page)
    hits = [v for v in REMOVED_VIEWS if re.search(rf"\b{v}\b", text)]
    assert not hits, f"{page.relative_to(ROOT)} still names removed view(s): {hits}"


def _public_files() -> list[Path]:
    files = [p for d in ("docs/img", "landing") for p in (ROOT / d).rglob("*") if p.is_file()]
    assert files, "no files found under docs/img or landing — the guard would check nothing"
    return files


def test_public_media_and_pages_carry_no_private_strings():
    leaks = []
    for path in _public_files():
        data = path.read_bytes()
        leaks += [f"{path.relative_to(ROOT)}: {n.decode()}" for n in PRIVATE_NEEDLES if n in data]
    assert not leaks, "private strings in public files:\n" + "\n".join(leaks)


def test_demo_media_exists_at_the_published_paths():
    for rel in ("docs/img/chela-demo-desktop.gif", "docs/img/chela-demo-mobile.gif",
                "landing/chela-demo-desktop.mp4", "landing/chela-demo-mobile.mp4"):
        p = ROOT / rel
        assert p.is_file(), rel
        if p.suffix == ".gif":
            assert p.stat().st_size <= 8 * 1024 * 1024, f"{rel} is over the 8 MB README budget"


# --- the recorder's isolation ------------------------------------------------

def _fleet():
    spec = importlib.util.spec_from_file_location("chela_demo_fleet", ROOT / "scripts" / "demo" / "fleet.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_demo_fleet_tmux_shim_pins_its_own_server(tmp_path):
    fleet = _fleet()
    fleet.write_shims(tmp_path)
    shim = (tmp_path / ".local" / "bin" / "tmux").read_text()
    assert f"-L {fleet.TMUX_SOCKET}" in shim
    assert fleet.TMUX_SOCKET and fleet.TMUX_SOCKET != "default"


def test_demo_fleet_env_is_temp_and_from_scratch(tmp_path, monkeypatch):
    # Anything the operator's shell carries must stay out — tmux pointers, chela config,
    # tokens, agent sockets, locale overrides alike.
    leaky = {"TMUX": "/tmp/tmux-1000/default,1,0", "TMUX_PANE": "%1",
             "CHELA_SECRET_PROBE": "must-not-leak", "GH_TOKEN": "ghp_probe",
             "ANTHROPIC_API_KEY": "sk-probe", "SSH_AUTH_SOCK": "/tmp/ssh-probe",
             "LC_ALL": "C.probe", "XDG_RUNTIME_DIR": "/run/user/probe", "USER": "probe-user"}
    for k, v in leaky.items():
        monkeypatch.setenv(k, v)
    # ...and EVERY variable already set: a value that reaches the demo env is inherited.
    for k in list(os.environ):
        if k not in leaky and k not in ("PATH", "LANG"):
            monkeypatch.setenv(k, f"probe-{k}")
    fleet = _fleet()
    env = fleet.demo_env(tmp_path, 5999, 6400)

    for key in ("CHELA_DIR", "HOME", "CHELA_DEMO_STATUS_DIR", "PYTHONPATH",
                "CHELA_DISPATCH_WORKFLOWS"):
        assert Path(env[key]).is_relative_to(tmp_path), f"{key}={env[key]} escapes the demo root"
    real_chela = Path(os.path.expanduser("~")) / ".chela"
    assert Path(env["CHELA_DIR"]) != real_chela
    # The shim dir comes first, so every `tmux` the demo runs is the pinned one.
    assert env["PATH"].split(":")[0] == str(tmp_path / ".local" / "bin")
    # From scratch: no probe key, and no value copied from the operator's env except
    # the two it is built on (PATH is prefixed, LANG is the locale).
    assert not set(leaky) & set(env), set(leaky) & set(env)
    assert "probe" not in "".join(env.values())
    # git in the demo never reads the operator's config (their name/email would show).
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["HOME"] == str(tmp_path)
    # The KEY SET is exact. A sentinel only lands on a variable that is SET on the test
    # machine; one that is unset here (XDG_CONFIG_HOME, say — git reads the operator's
    # identity from it) inherits as its `os.environ.get(..., "")` fallback and carries
    # no sentinel. Any key outside this list is an inheritance, set or not.
    assert set(env) == DEMO_ENV_KEYS, sorted(set(env) ^ DEMO_ENV_KEYS)


def test_demo_fleet_env_inherits_no_var_unset_on_the_test_machine(tmp_path, monkeypatch):
    # The shape-402 hole from the other side: variables ABSENT from os.environ.
    for k in ("XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_DIR", "GNUPGHOME"):
        monkeypatch.delenv(k, raising=False)
    env = _fleet().demo_env(tmp_path, 5999, 6400)
    assert not {"XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_DIR", "GNUPGHOME"} & set(env)
    assert set(env) == DEMO_ENV_KEYS


def test_demo_fleet_root_is_fixed_and_ignores_tmpdir(tmp_path, monkeypatch):
    """The root is /tmp/demo (or $CHELA_DEMO_ROOT) — NOT $TMPDIR, which on the
    operator's machine is a scratch dir named after them, and NOT a random suffix
    the Work view would print verbatim."""
    # TMPDIR must be a REAL writable dir: gettempdir() silently skips one that does not
    # exist and falls back to /tmp — so a made-up path makes `gettempdir()/"demo"` equal
    # /tmp/demo, and the guard cannot tell the two roots apart.
    scratch = tmp_path / "claude-1000" / "-home-someone-projects"
    scratch.mkdir(parents=True)
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.delenv("CHELA_DEMO_ROOT", raising=False)
    fleet = _fleet()
    assert fleet.DEFAULT_ROOT == Path("/tmp/demo")
    import tempfile
    tempfile.tempdir = None  # make gettempdir() re-read the patched TMPDIR
    try:
        # Precondition: the fixture really moved the temp dir, or nothing below can fail.
        assert Path(tempfile.gettempdir()) == scratch
        made = []
        monkeypatch.setattr(fleet.Path, "mkdir", lambda self, **kw: made.append(self))
        monkeypatch.setattr(fleet.Path, "write_text", lambda self, *a, **kw: None)
        monkeypatch.setattr(fleet.Path, "exists", lambda self: False)
        assert fleet.make_root() == Path("/tmp/demo")
        assert made == [Path("/tmp/demo")]
    finally:
        tempfile.tempdir = None


def test_demo_fleet_root_refuses_what_it_did_not_build(tmp_path):
    fleet = _fleet()
    foreign = tmp_path / "somebody-elses"
    foreign.mkdir()
    (foreign / "precious.txt").write_text("keep me")
    with pytest.raises(SystemExit):
        fleet.make_root(foreign)
    assert (foreign / "precious.txt").read_text() == "keep me"

    # A HOME of our own, so a disabled refusal writes into tmp_path, never the real ~.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", str(fake_home))
        with pytest.raises(SystemExit):
            fleet.make_root(fake_home / "demo")
    assert not (fake_home / "demo").exists()

    ours = fleet.make_root(tmp_path / "demo")
    (ours / "stale").write_text("from a previous run")
    again = fleet.make_root(tmp_path / "demo")
    assert (again / fleet.MARKER).is_file() and not (again / "stale").exists()


def test_demo_fleet_seeds_the_launcher_with_demo_projects(tmp_path):
    fleet = _fleet()
    env = fleet.demo_env(tmp_path, 5999, 6400)
    Path(env["CHELA_DIR"]).mkdir(parents=True)
    fleet.seed_launcher(env)
    store = json.loads((Path(env["CHELA_DIR"]) / "launcher.json").read_text())
    paths = [e["path"] for e in store["favorites"] + store["recent"]]
    assert sorted(Path(p).name for p in paths) == sorted(n for n, _ in fleet.AGENTS)
    assert all(Path(p).is_relative_to(tmp_path) for p in paths)


def test_demo_fleet_up_wires_every_step(tmp_path, monkeypatch):
    """up() end to end, with every process stubbed: the helpers above are only worth
    something if up() actually calls them — launcher seeded, app built as a git
    checkout, and the daemon on the INERT idle workflow, never api-server's."""
    fleet = _fleet()
    monkeypatch.setenv("CHELA_DEMO_ROOT", str(tmp_path / "demo"))
    monkeypatch.setattr(fleet, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(fleet, "_wait_http", lambda port: None)
    runs, popens = [], []

    def fake_run(args, **kw):
        runs.append((list(args), kw))
        return fleet.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    class FakePopen:
        def __init__(self, args, **kw):
            popens.append((list(args), kw))
            self.pid = 10**6 + len(popens)

    monkeypatch.setattr(fleet.subprocess, "run", fake_run)
    monkeypatch.setattr(fleet.subprocess, "Popen", FakePopen)
    state = fleet.up()

    env = state["env"]
    root = tmp_path / "demo"
    assert state["root"] == str(root)
    # seed_launcher ran: the "+" menu offers the demo projects.
    store = json.loads((Path(env["CHELA_DIR"]) / "launcher.json").read_text())
    assert sorted(Path(e["path"]).name for e in store["favorites"] + store["recent"]) == \
        sorted(n for n, _ in fleet.AGENTS)
    # make_app ran: the package was copied and committed into its own repo with an upstream.
    app = Path(env["PYTHONPATH"])
    assert (app / "chela" / "__init__.py").is_file()
    git = [a[1:] for a, kw in runs if a[0] == "git"]
    assert ["init", "-q", "-b", "main"] in git
    assert ["branch", "-q", "--set-upstream-to=origin/main", "main"] in git
    assert all(kw["cwd"] in (str(app), str(app.parent)) for a, kw in runs if a[0] == "git")
    # seed ran inside the demo env.
    assert any(a[0] == env["PYTHON"] and a[1] == "-c" and kw["env"] is env for a, kw in runs)
    # Every tmux call went through the demo env (whose PATH puts the pinned shim first).
    tmux_calls = [kw for a, kw in runs if a[0] == "tmux"]
    assert tmux_calls and all(kw["env"] is env for kw in tmux_calls)
    # The three services, each on the demo env; the daemon on the INERT idle workflow.
    by_cmd = {a[-1] if a[0] == "bash" else a[3]: kw for a, kw in popens}
    assert set(by_cmd) == {"run", str(fleet.REPO / "scripts" / "agent-terminals.sh"), "dashboard"}
    daemon = by_cmd["run"]["env"]
    idle = root / ".cache" / "chela-demo" / "idle" / "WORKFLOW.md"
    assert daemon["CHELA_DISPATCH_WORKFLOWS"] == str(idle) != env["CHELA_DISPATCH_WORKFLOWS"]
    assert idle.is_file() and (idle.parent / "TODO.md").read_text() == "# TODO\n"
    assert {k: v for k, v in daemon.items() if k != "CHELA_DISPATCH_WORKFLOWS"} == \
        {k: v for k, v in env.items() if k != "CHELA_DISPATCH_WORKFLOWS"}
    assert by_cmd["dashboard"]["env"] is env
    assert by_cmd["dashboard"]["cwd"] == str(root / "api-server")
    assert json.loads((tmp_path / "state.json").read_text())["root"] == str(root)
