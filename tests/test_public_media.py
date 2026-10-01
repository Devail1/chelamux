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
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_PAGES = [ROOT / "README.md", ROOT / "landing" / "index.html", ROOT / "landing" / "docs.html"]

# Case-sensitive on purpose: these are view NAMES. "zero-knowledge relay" and
# "feed" as a verb are ordinary words, not a nav view.
REMOVED_VIEWS = ["Feed", "Knowledge", "Personas", "Cost"]

# The real subprocess entry points, kept before any test stubs the module attributes.
_RUN, _POPEN = subprocess.run, subprocess.Popen

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
    files = [p for d in ("docs/img", "landing", "skills") for p in (ROOT / d).rglob("*")
             if p.is_file()]
    files.append(ROOT / "README.md")   # the README is the most public page of all
    assert files, "no files found under docs/img or landing — the guard would check nothing"
    return files


def test_the_private_strings_sweep_covers_every_shipped_skill():
    """CMX-415: skills are copied into adopters' ~/.claude — as public as the README."""
    swept = set(_public_files())
    skills = [p for p in (ROOT / "skills").rglob("*") if p.is_file()]
    assert skills and all(p in swept for p in skills), (
        f"not swept: {[str(p.relative_to(ROOT)) for p in skills if p not in swept]}")


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


def _argv_echo(path: Path) -> Path:
    """A stand-in for a real binary that prints the argv it was exec'd with."""
    path.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n')
    path.chmod(0o755)
    return path


def _assert_shims_pin_the_demo(bin_dir: Path, fleet) -> None:
    """EXECUTE the shims (a substring check would pass for a flag in a comment): the
    tmux one must exec the real tmux with ``-L <demo socket>`` ahead of the caller's
    args, and pgrep must not find the host's own chela services."""
    out = _RUN([str(bin_dir / "tmux"), "list-windows", "-t", "chela"],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert out == ["-L", fleet.TMUX_SOCKET, "list-windows", "-t", "chela"], out
    assert fleet.TMUX_SOCKET and fleet.TMUX_SOCKET != "default"
    # Every chela service the dashboard probes, not only the daemon — Settings asks
    # `pgrep -f "chela telegram"` (app.py), and a hit there puts the operator's REAL
    # bridge on camera as "connected".
    for svc in ("chela run", "chela telegram", "chela dashboard"):
        hidden = _RUN([str(bin_dir / "pgrep"), "-f", svc], capture_output=True, text=True)
        assert hidden.returncode == 1 and hidden.stdout == "", svc
    other = _RUN([str(bin_dir / "pgrep"), "-f", "ttyd"], capture_output=True, text=True)
    assert other.stdout.splitlines() == ["-f", "ttyd"]  # anything else reaches the real pgrep
    _assert_stubs_answer_for_the_demo(bin_dir, tmp=bin_dir.parent)


DEMO_SHIMS = {"tmux", "claude", "gh", "crontab", "pgrep", "pm2"}


def _assert_stubs_answer_for_the_demo(bin_dir: Path, tmp: Path) -> None:
    """EXECUTE every other shim too — each stands between a view on camera and the
    operator's real account: crontab is per-USER (the demo HOME does not isolate it) and
    the Schedules view runs ``crontab -l``; Settings runs ``gh auth status``; the Wall
    asks ``claude agents --json``. A missing or renamed shim lets the real one answer."""
    assert {p.name for p in bin_dir.iterdir()} == DEMO_SHIMS
    empty = tmp / "no-status"
    empty.mkdir(exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "CHELA_DEMO_STATUS_DIR": str(empty)}

    def run(*argv: str):
        return _RUN([str(bin_dir / argv[0]), *argv[1:]], capture_output=True, text=True, env=env)

    cron = run("crontab", "-l")
    assert (cron.returncode, cron.stdout) == (0, ""), cron  # an EMPTY crontab, not the user's
    gh = run("gh", "auth", "status")
    assert gh.returncode == 1 and gh.stdout == "" and "chela demo: gh is stubbed" in gh.stderr, gh
    agents = run("claude", "agents", "--json")
    assert (agents.returncode, json.loads(agents.stdout)) == (0, []), agents
    other = run("claude", "-p", "hello")  # nothing else reaches a model or an account
    assert other.returncode == 2 and "fake claude implements only" in other.stderr, other
    pm2 = run("pm2", "jlist")
    assert (pm2.returncode, json.loads(pm2.stdout)) == (0, []), pm2


def _fake_which(tmp_path: Path):
    real = {"tmux": _argv_echo(tmp_path / "real-tmux"), "pgrep": _argv_echo(tmp_path / "real-pgrep")}
    return lambda name, *a, **kw: str(real[name]) if name in real else None


def test_demo_fleet_tmux_shim_pins_its_own_server(tmp_path, monkeypatch):
    fleet = _fleet()
    monkeypatch.setattr(fleet.shutil, "which", _fake_which(tmp_path))
    root = tmp_path / "root"
    fleet.write_shims(root)
    _assert_shims_pin_the_demo(root / ".local" / "bin", fleet)


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
    # Plus every key the demo env itself emits, set or not on this machine: a sentinel
    # only lands on a SET variable, so `os.environ.get("GIT_AUTHOR_EMAIL", default)`
    # would otherwise read the unset var and return the very default we expect.
    for k in set(os.environ) | DEMO_ENV_KEYS:
        if k not in leaky and k not in ("PATH", "LANG"):
            monkeypatch.setenv(k, f"probe-{k}")
    fleet = _fleet()
    env = fleet.demo_env(tmp_path, 5999, 6400)
    assert not set(leaky) & set(env), set(leaky) & set(env)
    _assert_demo_env_is_from_scratch(tmp_path, env)


def _assert_demo_env_is_from_scratch(tmp_path, env) -> None:
    for key in ("CHELA_DIR", "HOME", "CHELA_DEMO_STATUS_DIR", "PYTHONPATH",
                "CHELA_DISPATCH_WORKFLOWS"):
        assert Path(env[key]).is_relative_to(tmp_path), f"{key}={env[key]} escapes the demo root"
    real_chela = Path(os.path.expanduser("~")) / ".chela"
    assert Path(env["CHELA_DIR"]) != real_chela
    # The shim dir comes first, so every `tmux` the demo runs is the pinned one.
    assert env["PATH"].split(":")[0] == str(tmp_path / ".local" / "bin")
    # The dashboard stays on loopback during a recording, with Remote Control off.
    assert env["CHELA_DASH_HOST"] == "127.0.0.1"
    assert env["CHELA_REMOTE_CONTROL"] == "false"
    # From scratch: no probe key, and no value copied from the operator's env except
    # the two it is built on (PATH is prefixed, LANG is the locale).
    assert "probe" not in "".join(env.values())
    # git in the demo never reads the operator's config (their name/email would show).
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["HOME"] == str(tmp_path)
    # The identity on the demo's commits is the neutral one, exactly.
    assert {k: env[k] for k in env if k.startswith("GIT_") and k != "GIT_CONFIG_NOSYSTEM"} == {
        "GIT_AUTHOR_NAME": "chela demo", "GIT_AUTHOR_EMAIL": "demo@example.com",
        "GIT_COMMITTER_NAME": "chela demo", "GIT_COMMITTER_EMAIL": "demo@example.com"}
    # The KEY SET is exact. A sentinel only lands on a variable that is SET on the test
    # machine; one that is unset here (XDG_CONFIG_HOME, say — git reads the operator's
    # identity from it) inherits as its `os.environ.get(..., "")` fallback and carries
    # no sentinel. Any key outside this list is an inheritance, set or not.
    assert set(env) == DEMO_ENV_KEYS, sorted(set(env) ^ DEMO_ENV_KEYS)


class _ProbeEnviron(Mapping):
    """A stand-in for ``os.environ`` in which EVERY variable is set — to ``probe-<name>``
    — except the two the demo env is built on. Planting sentinels on names only covers
    the names someone thought of (shape 402d: git reads ``$EMAIL`` too); here any
    ``os.environ[...]`` / ``.get(name, default)`` read of any name carries a probe, so
    no fallback default can mask an inheritance."""

    _REAL = ("PATH", "LANG")

    def __getitem__(self, k):
        return os.environ[k] if k in self._REAL and k in os.environ else f"probe-{k}"

    def __iter__(self):
        return iter(os.environ)

    def __len__(self):
        return len(os.environ)


def test_demo_fleet_env_reads_no_operator_var_of_any_name(tmp_path, monkeypatch):
    fleet = _fleet()
    # Only fleet's view of `os` changes; the rest of the process keeps the real one.
    monkeypatch.setattr(fleet, "os", SimpleNamespace(
        **{**{n: getattr(os, n) for n in dir(os) if not n.startswith("__")},
           "environ": _ProbeEnviron()}))
    env = fleet.demo_env(tmp_path, 5999, 6400)
    _assert_demo_env_is_from_scratch(tmp_path, env)


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
    monkeypatch.setattr(fleet.shutil, "which", _fake_which(tmp_path))
    runs, popens = [], []

    def fake_run(args, **kw):
        runs.append((list(args), kw))
        return fleet.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    popen_pids = []

    class FakePopen:
        def __init__(self, args, **kw):
            popens.append((list(args), kw))
            self.pid = 10**6 + len(popens)
            popen_pids.append(self.pid)

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
    # ...and up() really installed that shim there: a bare `tmux` on this PATH would
    # otherwise be the operator's own server. (Real subprocess back, to execute it.)
    monkeypatch.setattr(fleet.subprocess, "Popen", _POPEN)
    _assert_shims_pin_the_demo(Path(env["PATH"].split(":")[0]), fleet)
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
    # agent-terminals.sh too: without the demo PATH its `tmux` is the operator's own,
    # and ttyd would put the REAL chela session on camera.
    assert by_cmd[str(fleet.REPO / "scripts" / "agent-terminals.sh")]["env"] is env
    assert by_cmd["dashboard"]["cwd"] == str(root / "api-server")
    # Both `python -m chela.main` services start OUTSIDE this checkout: `-m` puts the cwd
    # ahead of PYTHONPATH, so a cwd here would import the REAL repo's chela, not the copy.
    assert by_cmd["run"]["cwd"] == str(root / ".cache" / "chela-demo" / "idle")
    for svc in ("run", "dashboard"):
        assert not Path(by_cmd[svc]["cwd"]).resolve().is_relative_to(fleet.REPO.resolve()), svc
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["root"] == str(root)
    # The invariants, over EVERY call — not a hand-picked few:
    # (a) the state file records every service up() started, so down() can reach it.
    started = {k: p for k, p in zip(("daemon", "terminals", "dashboard"), popen_pids)}
    assert saved["pids"] == started and len(set(started.values())) == 3
    # (b) every service leads its OWN process group — down() signals the group (killpg),
    #     so a service that shares ours is never reached.
    assert all(kw.get("start_new_session") is True for a, kw in popens)
    # (c) every subprocess (git, tmux, seed, services) runs on the demo env — never the
    #     inherited one, which carries the operator's git identity, tmux socket and CHELA_DIR.
    for a, kw in runs + popens:
        assert kw.get("env") is not None, a
        assert {k: v for k, v in kw["env"].items() if k != "CHELA_DISPATCH_WORKFLOWS"} == \
            {k: v for k, v in env.items() if k != "CHELA_DISPATCH_WORKFLOWS"}, a
    # (d) nothing that imports chela runs from THIS checkout (`python -m` / `-c` put the
    #     cwd ahead of PYTHONPATH).
    for a, kw in runs + popens:
        if a[0] == env["PYTHON"]:
            assert not Path(kw["cwd"]).resolve().is_relative_to(fleet.REPO.resolve()), a


def test_demo_fleet_app_remote_is_its_own_bare_clone(tmp_path):
    """make_app with REAL git: the demo app's origin is the local bare clone beside it —
    never this checkout, whose path names the operator and would show in Settings'
    Update section — and nothing in its git config points back at the real repo."""
    fleet = _fleet()
    root = tmp_path / "demo"
    env = fleet.demo_env(root, 5999, 6400)
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")  # the real git; no shims here
    Path(env["HOME"]).mkdir(parents=True)
    fleet.make_app(env)
    app = Path(env["PYTHONPATH"])

    def git(*args: str) -> str:
        return _RUN(["git", *args], env=env, cwd=str(app), capture_output=True,
                    text=True, check=True).stdout.strip()

    origin = app.parent / "chela-origin.git"
    assert git("remote") == "origin"
    assert Path(git("remote", "get-url", "origin")) == origin
    assert (origin / "HEAD").is_file()
    assert git("rev-parse", "--abbrev-ref", "main@{upstream}") == "origin/main"
    config = (app / ".git" / "config").read_text()
    assert str(fleet.REPO) not in config and str(ROOT) not in config, config


def _down_with_state(fleet, tmp_path, monkeypatch, root: Path) -> None:
    monkeypatch.setattr(fleet, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(fleet.time, "sleep", lambda s: None)
    (tmp_path / "state.json").write_text(json.dumps({"root": str(root), "pids": {}, "env": {}}))
    fleet.down()
    assert not (tmp_path / "state.json").exists()


def test_demo_fleet_down_never_deletes_a_root_it_did_not_build(tmp_path, monkeypatch):
    """`down` rmtrees the root named in its state file only if that root carries MARKER,
    so a hand-edited state or a mistyped CHELA_DEMO_ROOT can never take real data."""
    fleet = _fleet()
    foreign = tmp_path / "somebody-elses"
    foreign.mkdir()
    (foreign / "precious.txt").write_text("keep me")
    _down_with_state(fleet, tmp_path, monkeypatch, foreign)
    assert (foreign / "precious.txt").read_text() == "keep me"

    # Positive control: the same call does remove a root fleet.py built.
    ours = fleet.make_root(tmp_path / "demo")
    _down_with_state(fleet, tmp_path, monkeypatch, ours)
    assert not ours.exists()


def test_demo_fleet_down_kills_only_the_demo_tmux_server(tmp_path, monkeypatch):
    """`tmux kill-server` without the demo env would kill the operator's REAL server —
    every agent. It must run on the state's env, whose PATH puts the pinned shim first."""
    fleet = _fleet()
    monkeypatch.setattr(fleet, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(fleet.time, "sleep", lambda s: None)
    env = fleet.demo_env(tmp_path / "demo", 5999, 6400)
    (tmp_path / "state.json").write_text(json.dumps({"root": "", "pids": {}, "env": env}))
    runs = []
    monkeypatch.setattr(fleet.subprocess, "run", lambda args, **kw: runs.append((list(args), kw)))
    fleet.down()
    kills = [kw for a, kw in runs if a == ["tmux", "kill-server"]]
    assert len(kills) == 1 and kills[0].get("env") == env
    assert kills[0]["env"]["PATH"].split(":")[0] == str(tmp_path / "demo" / ".local" / "bin")


def test_demo_fleet_down_kills_every_recorded_service_group(tmp_path, monkeypatch):
    """`down` must SIGTERM the process group of every service `up` recorded — otherwise the
    demo daemon, ttyd and dashboard outlive the demo and keep its port."""
    fleet = _fleet()
    monkeypatch.setattr(fleet, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(fleet.time, "sleep", lambda s: None)
    monkeypatch.setattr(fleet.subprocess, "run", lambda *a, **kw: None)
    pids = {"daemon": 424201, "terminals": 424202, "dashboard": 424203}
    (tmp_path / "state.json").write_text(json.dumps({"root": "", "pids": pids, "env": {}}))
    killed = []
    monkeypatch.setattr(fleet.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    fleet.down()
    assert sorted(killed) == sorted((pid, fleet.signal.SIGTERM) for pid in pids.values())


def _node_or_skip() -> str:
    import shutil as _sh
    node = _sh.which("node")
    if not node:
        if os.environ.get("CHELA_REQUIRE_JS_TESTS") == "1":
            pytest.fail("node is required (CHELA_REQUIRE_JS_TESTS=1)")
        pytest.skip("node not installed")
    return node


_RECORD_MJS = Path(__file__).resolve().parent.parent / "scripts" / "demo" / "record.mjs"


def _record_mjs(url: str, tmp_path: Path, state: dict | None):
    """Run record.mjs's FENCE for real (CHELA_DEMO_CHECK_ONLY stops it before a browser)."""
    state_path = tmp_path / "state.json"
    if state is not None:
        state_path.write_text(json.dumps(state))
    env = {**os.environ, "CHELA_DEMO_STATE": str(state_path), "CHELA_DEMO_CHECK_ONLY": "1"}
    return _RUN([_node_or_skip(), str(_RECORD_MJS), url, str(tmp_path / "out")], env=env,
                capture_output=True, text=True, timeout=30, cwd=str(_RECORD_MJS.parents[2]))


def _live_demo(tmp_path: Path, url: str) -> dict:
    root = tmp_path / "demo"
    root.mkdir(exist_ok=True)
    (root / _fleet().MARKER).write_text("x")
    return {"root": str(root), "url": url, "pids": {}, "env": {}}


def test_record_mjs_records_ONLY_the_running_demo_fleets_own_dashboard(tmp_path):
    """record.mjs must never record the operator's real dashboard — which is ALSO on
    loopback. It accepts exactly the URL a live demo fleet (marker-carrying root) wrote to
    its state file; everything else is refused before any browser starts. EXECUTED."""
    demo = "http://127.0.0.1:5999/"
    live = _live_demo(tmp_path, demo)
    # ⭐ ACCEPTED: the demo's own URL (with or without the trailing slash).
    for url in (demo, demo.rstrip("/")):
        r = _record_mjs(url, tmp_path, live)
        assert r.returncode == 0, (url, r.stderr[-300:])
    refused = {
        # the operator's REAL dashboard: loopback, but not the demo's
        "http://127.0.0.1:5001/": live,
        # not loopback at all / not http / userinfo trick / trailing path
        "http://localhost:5999/": live,
        "http://10.0.0.1:5999/": live,
        "https://127.0.0.1:5999/": live,
        "http://127.0.0.1:5999@10.0.0.1:5999/": {**live, "url": "http://127.0.0.1:5999@10.0.0.1:5999/"},
        "http://127.0.0.1:5999/../x": {**live, "url": "http://127.0.0.1:5999/../x"},
    }
    for url, state in refused.items():
        r = _record_mjs(url, tmp_path, state)
        assert r.returncode == 2 and "refusing" in r.stderr, (url, r.returncode, r.stderr[-300:])
    # no demo running (no state file), or a root fleet.py did not build (no marker)
    (tmp_path / "state.json").unlink()
    r = _record_mjs(demo, tmp_path, None)
    assert r.returncode == 2 and "refusing" in r.stderr
    (tmp_path / "demo" / _fleet().MARKER).unlink()
    r = _record_mjs(demo, tmp_path, live)
    assert r.returncode == 2 and "refusing" in r.stderr
    assert not (tmp_path / "out").exists()


def test_demo_fleet_re_up_tears_the_previous_demo_down_FIRST(tmp_path, monkeypatch):
    """A second `up` must stop the previous demo (services + demo tmux server) BEFORE it
    rebuilds the root — else it rmtrees a root under processes that are still running."""
    fleet = _fleet()
    monkeypatch.setattr(fleet, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text(json.dumps({"root": "", "pids": {}, "env": {}}))
    order = []
    monkeypatch.setattr(fleet, "down", lambda: order.append("down"))

    def stop(*a, **k):
        order.append("make_root")
        raise SystemExit(0)
    monkeypatch.setattr(fleet, "make_root", stop)
    with pytest.raises(SystemExit):
        fleet.up()
    assert order == ["down", "make_root"]

    # Positive control: with no previous state, up() does not call down().
    (tmp_path / "state.json").unlink()
    order.clear()
    with pytest.raises(SystemExit):
        fleet.up()
    assert order == ["make_root"]


@pytest.mark.parametrize("bad", ["demo", "./demo", "../demo"])
def test_demo_fleet_root_refuses_a_RELATIVE_root_and_creates_nothing(tmp_path, monkeypatch, bad):
    """A relative root resolves inside the cwd — this checkout, whose path names the
    operator, and the Work view prints demo paths verbatim."""
    fleet = _fleet()
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="must be absolute"):
        fleet.make_root(Path(bad))
    assert list(tmp_path.iterdir()) == []


def _fake_repo_for_record_sh(tmp_path: Path, up_exit: int, node_exit: int) -> tuple[Path, Path]:
    """A throwaway REPO layout around a COPY of record.sh: its python and node are stubs
    that log their argv, so the script runs for real but touches nothing."""
    repo = tmp_path / "repo"
    (repo / "scripts" / "demo").mkdir(parents=True)
    real = Path(__file__).resolve().parent.parent / "scripts" / "demo" / "record.sh"
    (repo / "scripts" / "demo" / "record.sh").write_text(real.read_text())
    log = tmp_path / "calls.log"
    py = repo / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/sh\necho "python $*" >> {log}\n'
                  f'case "$2" in up) echo http://127.0.0.1:1/; exit {up_exit};; esac\nexit 0\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "node").write_text(f'#!/bin/sh\necho "node $*" >> {log}\nexit {node_exit}\n')
    for f in (py, bin_dir / "node"):
        f.chmod(0o755)
    return repo, log


@pytest.mark.parametrize("up_exit,node_exit", [(1, 0), (0, 1), (0, 0)])
def test_record_sh_ALWAYS_tears_the_fleet_down(tmp_path, up_exit, node_exit):
    """Step 4 of record.sh: `fleet.py down` runs on EVERY exit — a failed `up`, a failed
    recording, and success — or the demo daemon, dashboard and tmux server outlive it.
    EXECUTED with stubbed python/node, not grepped."""
    repo, log = _fake_repo_for_record_sh(tmp_path, up_exit, node_exit)
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path),
           "OUT": str(tmp_path / "out")}
    r = _RUN(["bash", str(repo / "scripts" / "demo" / "record.sh")], env=env,
             capture_output=True, text=True, timeout=60)
    calls = log.read_text().splitlines()
    downs = [c for c in calls if c.endswith("fleet.py down")]
    assert len(downs) == 1 and calls[-1] == downs[0], (r.returncode, calls, r.stderr[-400:])
    assert (r.returncode != 0) == bool(up_exit or node_exit)
