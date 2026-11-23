"""CMX-477 — the chela-measure mod feeds the context bar and the 5h/7d limit bars.

The mod's own ``claude plugin test`` cases (``chela/mods/chela-measure/hooks/measure.test.ts``)
prove that a ``session.measure`` input becomes ``file`` (``measure-cases.ts``). These feed
that same recorded file through the REAL readers (``chela.context``, ``chela.usage``) beside
the statusLine's, and cover ``chela install-mod`` and the doctor fact.
"""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from chela import config, context, main, measure_mod, runtime_truth, usage
from chela.runtime_truth import OK, WARN

REPO = Path(__file__).resolve().parent.parent
MOD = REPO / "chela" / "mods" / "chela-measure"
MEASURE = json.loads(
    (MOD / "hooks" / "measure-cases.ts").read_text()
    .split("export const MEASURE =", 1)[1].rsplit("as const", 1)[0])
SID = MEASURE["session_id"]
# The figures the recorded payload must read as, written out (never the reader's output).
FIGURES = {"used_pct": 42, "total_k": 200.0, "used_k": 84.0, "free_pct": 58, "free_k": 116.0,
           "cost_usd": 1.25, "rate_limit_pct": 23.5, "rate_limit_resets_at": 1792238400,
           "weekly_rl_pct": 7, "weekly_rl_resets_at": 1792454400, "model": "claude-opus-5-5"}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    d = tmp_path / "context"
    monkeypatch.setattr(context, "CONTEXT_CACHE_DIR", d)
    monkeypatch.setattr(context, "DB_PATH", tmp_path / "scheduler.db")
    return d


def _write(path: Path, payload: dict, age: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    t = time.time() - age
    os.utime(path, (t, t))
    return path


def _mod(ctx: Path, payload: dict | None = None, age: float = 0.0) -> Path:
    return _write(ctx / "mod" / f"{SID}.json", payload or MEASURE["file"], age)


def _statusline(ctx: Path, pct: float, five: float, age: float = 0.0) -> Path:
    payload = {**MEASURE["file"], "context_window": {**MEASURE["file"]["context_window"],
                                                    "used_percentage": pct},
               "rate_limits": {"five_hour": {"used_percentage": five, "resets_at": 1792238400},
                               "seven_day": {"used_percentage": 7, "resets_at": 1792454400}}}
    payload.pop("source")
    return _write(ctx / "by-session" / f"{SID}.json", payload, age)


def _limits(ctx: Path) -> dict:
    return usage.limits(ctx, now=time.time(), history=False)


# --- the fixture through the snapshot path -----------------------------------------------

def test_the_recorded_payload_is_the_documented_literal():
    assert MEASURE["file"]["source"] == "mod"
    assert MEASURE["event"]["rateLimits"][0] == {
        "kind": "five_hour", "percentUsed": 23.5, "resetsAt": "2026-10-17T12:00:00.750Z"}


def test_mod_only_the_context_bar_reads_the_mod_file(ctx):
    """🔴 GUARD (acceptance): the mod installed and no statusLine — real figures, source mod."""
    _mod(ctx)
    snap = context._cache_snapshot("w", SID)
    assert snap is not None and snap["source"] == "mod" and snap["estimated"] is False
    assert {k: snap[k] for k in FIGURES} == FIGURES
    assert "_mtime" not in snap


def test_mod_only_the_limit_bars_show_real_values(ctx, monkeypatch):
    """🔴 GUARD (acceptance): 5h/7d from the mod alone, each saying where it came from."""
    monkeypatch.setattr(time, "time", lambda: 1792238400 - 3600)
    _write(ctx / "mod" / f"{SID}.json", MEASURE["file"])
    lim = usage.limits(ctx, now=1792238400 - 3600, history=False)
    assert (lim["five_hour"]["used_pct"], lim["five_hour"]["resets_at"], lim["five_hour"]["source"]) \
        == (23.5, 1792238400, "mod")
    assert (lim["seven_day"]["used_pct"], lim["seven_day"]["source"]) == (7, "mod")
    assert set(lim) == {"five_hour", "seven_day"}           # spend_limit is not a bar


def test_neither_producer_the_bars_are_unknown_never_zero(ctx):
    """🔴 GUARD (acceptance): no statusLine, no mod — None with a reason, never 0%."""
    lim = _limits(ctx)
    for key in ("five_hour", "seven_day"):
        assert lim[key]["used_pct"] is None and lim[key]["source"] is None
        assert lim[key]["reason"] == "no statusLine or mod rate_limits found"
    assert context._cache_snapshot("w", SID) is None


@pytest.mark.parametrize("mod_age, sl_age, winner", [
    (5, 60, "mod"),            # the mod measured last
    (60, 5, "statusline"),     # the statusLine wrote last
])
def test_both_producers_the_fresher_one_wins(ctx, mod_age, sl_age, winner, monkeypatch):
    """🔴 GUARD (acceptance): with both installed, the NEWER snapshot is served, either way."""
    now = 1792238400 - 3600
    monkeypatch.setattr(time, "time", lambda: now)
    mod = _mod(ctx)
    sl = _statusline(ctx, pct=10, five=50)
    os.utime(mod, (now - mod_age, now - mod_age))
    os.utime(sl, (now - sl_age, now - sl_age))
    snap = context._cache_snapshot("w", SID)
    assert (snap["source"], snap["used_pct"]) == (winner, 42 if winner == "mod" else 10)
    lim = usage.limits(ctx, now=now, history=False)
    assert (lim["five_hour"]["used_pct"], lim["five_hour"]["source"]) == \
        ((23.5, "mod") if winner == "mod" else (50, "statusline"))


def test_a_tie_serves_the_statusline(ctx, monkeypatch):
    now = 1792238400 - 3600
    monkeypatch.setattr(time, "time", lambda: now)
    for p in (_mod(ctx), _statusline(ctx, pct=10, five=50)):
        os.utime(p, (now - 5, now - 5))
    assert context._cache_snapshot("w", SID)["source"] == "statusline"
    assert measure_mod.feeding({"mod": 5.0, "statusline": 5.0}) == "statusline"


def test_a_newer_mod_file_with_no_context_figure_falls_back_to_the_statusline(ctx):
    """Right after a compaction the mod has no percent: the older statusLine still serves."""
    _statusline(ctx, pct=10, five=50, age=60)
    no_pct = {**MEASURE["file"], "context_window": {"context_window_size": 200000}}
    _mod(ctx, no_pct, age=1)
    assert context._cache_snapshot("w", SID)["source"] == "statusline"


def test_a_stale_mod_file_is_not_served(ctx, monkeypatch):
    monkeypatch.setattr(config, "cache_stale_seconds", lambda: 600)
    _mod(ctx, age=601)
    assert context._cache_snapshot("w", SID) is None
    _mod(ctx, age=10)
    assert context._cache_snapshot("w", SID)["source"] == "mod"


def test_without_a_session_id_only_the_window_file_is_read(ctx):
    _mod(ctx)
    assert context._cache_snapshot("w") is None


def test_an_unsafe_session_id_never_becomes_a_path(ctx):
    _write(ctx / "mod" / "x.json", MEASURE["file"])
    assert context._mod_cache_path("../x") is None
    assert context._cache_snapshot("w", "../x") is None


def test_the_reported_window_reads_the_mod_file(ctx):
    """CMX-292's window size, from a mod-written file at any age."""
    _mod(ctx, age=10 * 86400)
    assert context._reported_window(SID) == (200000, "claude-opus-5-5")


def test_prune_drops_stale_mod_files_and_keeps_fresh_ones(ctx):
    old = _write(ctx / "mod" / "aaaaaaaa-old.json", MEASURE["file"], age=40 * 86400)
    new = _mod(ctx)
    old_sl = _write(ctx / "by-session" / "bbbbbbbb-old.json", MEASURE["file"], age=40 * 86400)
    context._prune_session_cache(30)
    assert (old.exists(), new.exists(), old_sl.exists()) == (False, True, False)


def test_prune_still_runs_over_the_mod_dir_when_by_session_cannot_be_listed(ctx, monkeypatch):
    old = _write(ctx / "mod" / "aaaaaaaa-old.json", MEASURE["file"], age=40 * 86400)
    real_glob = Path.glob

    def glob(self, pattern):
        if self.name == context.SESSION_CACHE_SUBDIR:
            raise PermissionError("denied")
        return real_glob(self, pattern)
    monkeypatch.setattr(Path, "glob", glob)
    context._prune_session_cache(30)
    assert not old.exists()


# --- chela install-mod ---------------------------------------------------------------------

@pytest.mark.parametrize("env, expected", [
    ("/cfg", Path("/cfg/skills/chela-measure")),
    ("  ", None),                       # blank is unset
    (None, None),
])
def test_the_install_dir_follows_claude_config_dir(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", env)
    assert measure_mod.install_dir() == (expected or Path.home() / ".claude" / "skills" / "chela-measure")


def test_install_copies_the_mod_records_chela_dir_and_leaves_tests_behind(tmp_path):
    dest = tmp_path / "skills" / "chela-measure"
    assert measure_mod.install(dest, tmp_path / "cd") == dest
    files = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    assert files == [".claude-plugin/plugin.json", "chela.json", "hooks/hooks.json",
                     "hooks/measure.ts", "hooks/register.ts"]
    assert json.loads((dest / "chela.json").read_text()) == {"chela_dir": str(tmp_path / "cd")}
    assert (dest / "hooks" / "measure.ts").read_bytes() == (MOD / "hooks" / "measure.ts").read_bytes()
    assert not list(dest.parent.glob(".chela-measure.*"))     # no temp folder left


def test_install_replaces_its_own_previous_copy_whole(tmp_path):
    dest = tmp_path / "chela-measure"
    measure_mod.install(dest, tmp_path / "a")
    (dest / "hooks" / "stale.ts").write_text("old")
    measure_mod.install(dest, tmp_path / "b")
    assert not (dest / "hooks" / "stale.ts").exists()
    assert json.loads((dest / "chela.json").read_text())["chela_dir"] == str(tmp_path / "b")


def test_install_refuses_to_replace_a_folder_that_is_not_the_mod(tmp_path):
    dest = tmp_path / "chela-measure"
    (dest / ".claude-plugin").mkdir(parents=True)
    (dest / ".claude-plugin" / "plugin.json").write_text('{"name": "someone-elses"}')
    with pytest.raises(FileExistsError):
        measure_mod.install(dest, tmp_path)
    assert json.loads((dest / ".claude-plugin" / "plugin.json").read_text())["name"] == "someone-elses"


def test_a_failed_copy_leaves_no_temp_folder_and_the_old_copy_intact(tmp_path, monkeypatch):
    dest = tmp_path / "chela-measure"
    measure_mod.install(dest, tmp_path / "a")

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(shutil, "copytree", full)
    with pytest.raises(OSError):
        measure_mod.install(dest, tmp_path / "b")
    assert not list(tmp_path.glob(".chela-measure.*"))
    assert json.loads((dest / "chela.json").read_text())["chela_dir"] == str(tmp_path / "a")


def test_install_refuses_a_missing_packaged_mod(tmp_path):
    with pytest.raises(FileNotFoundError):
        measure_mod.install(tmp_path / "d", tmp_path, src=tmp_path / "nope")
    assert not (tmp_path / "d").exists()


def test_install_refuses_a_packaged_folder_with_no_manifest(tmp_path):
    """A folder that exists but is not a plugin (a broken wheel) is never installed."""
    src = tmp_path / "src"
    (src / "hooks").mkdir(parents=True)
    (src / "hooks" / "register.ts").write_text("x")
    with pytest.raises(FileNotFoundError, match="the packaged mod is missing"):
        measure_mod.install(tmp_path / "d", tmp_path, src=src)
    assert not (tmp_path / "d").exists()


@pytest.mark.parametrize("manifest", [None, "not json", '["a list"]', '{"name": "other"}'])
def test_is_ours_reads_the_manifest_name_only(tmp_path, manifest):
    if manifest is not None:
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text(manifest)
    assert measure_mod.is_ours(tmp_path) is False


def test_remove(tmp_path):
    dest = tmp_path / "chela-measure"
    assert measure_mod.remove(dest) is False
    measure_mod.install(dest, tmp_path)
    assert measure_mod.remove(dest) is True and not dest.exists()
    dest.mkdir()
    with pytest.raises(FileExistsError):
        measure_mod.remove(dest)
    assert dest.exists()


def test_the_cli_installs_then_removes(tmp_path, monkeypatch, capsys):
    dest = tmp_path / "x" / "chela-measure"
    monkeypatch.setattr("sys.argv", ["chela", "install-mod", "--dir", str(dest)])
    main.main()
    out = capsys.readouterr().out
    assert f"installed chela-measure at {dest}" in out
    assert json.loads((dest / "chela.json").read_text()) == {"chela_dir": str(config.CHELA_DIR)}
    monkeypatch.setattr("sys.argv", ["chela", "install-mod", "--dir", str(dest), "--remove"])
    main.main()
    assert f"removed {dest}" in capsys.readouterr().out and not dest.exists()
    main.main()
    assert f"not installed at {dest}" in capsys.readouterr().out


def test_the_cli_default_dir_is_under_claude_config_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr("sys.argv", ["chela", "install-mod"])
    main.main()
    assert (tmp_path / "cfg" / "skills" / "chela-measure" / "hooks" / "register.ts").is_file()


def test_the_cli_exits_1_on_a_foreign_folder(tmp_path, monkeypatch, capsys):
    dest = tmp_path / "chela-measure"
    dest.mkdir()
    monkeypatch.setattr("sys.argv", ["chela", "install-mod", "--dir", str(dest)])
    with pytest.raises(SystemExit) as e:
        main.main()
    assert e.value.code == 1
    assert "not the chela-measure mod" in capsys.readouterr().err


def test_the_cli_exits_1_when_the_copy_fails(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise PermissionError("read-only")
    monkeypatch.setattr(measure_mod, "install", boom)
    monkeypatch.setattr("sys.argv", ["chela", "install-mod", "--dir", str(tmp_path / "d")])
    with pytest.raises(SystemExit) as e:
        main.main()
    assert e.value.code == 1 and "read-only" in capsys.readouterr().err


# --- doctor: which producer feeds each session --------------------------------------------

def test_session_sources_lists_fresh_files_per_producer(ctx):
    _mod(ctx, age=5)
    _statusline(ctx, pct=10, five=50, age=60)
    _write(ctx / "mod" / "cccccccc-stale.json", MEASURE["file"], age=10_000)
    got = measure_mod.session_sources(ctx, stale_s=600)
    assert set(got) == {SID}
    assert set(got[SID]) == {"mod", "statusline"}
    assert measure_mod.feeding(got[SID]) == "mod"


def test_session_sources_reads_the_staleness_knob_live(ctx, monkeypatch):
    _mod(ctx, age=100)
    monkeypatch.setattr(config, "cache_stale_seconds", lambda: 50)
    assert measure_mod.session_sources(ctx) == {}
    monkeypatch.setattr(config, "cache_stale_seconds", lambda: 500)
    assert set(measure_mod.session_sources(ctx)) == {SID}


@pytest.mark.parametrize("settings, wired", [
    ({"statusLine": {"type": "command", "command": "/h/.chela/cache-statusline.sh"}}, True),
    ({"statusLine": {"type": "command", "command": "/usr/bin/my-own-line"}}, False),
    ({"statusLine": "cache-statusline.sh"}, False),
    ({}, False),
    ("not json", False),
    (None, False),
])
def test_statusline_wired(tmp_path, settings, wired):
    path = tmp_path / "settings.json"
    if settings is not None:
        path.write_text(settings if isinstance(settings, str) else json.dumps(settings))
    assert measure_mod.statusline_wired(path) is wired


def _state(tmp_path, **over):
    lim = {k: {"used_pct": None, "reason": "no statusLine or mod rate_limits found", "source": None}
           for k in ("five_hour", "seven_day")}
    base = {"mod": None, "packaged_version": "0.1.0", "statusline": False, "limits": lim,
            "sessions": {}}
    return {**base, **over}


def test_doctor_with_nothing_installed_is_quiet_but_says_unknown(tmp_path):
    found = measure_mod.report(tmp_path, _state(tmp_path))
    assert all(f.level == OK for f in found)
    titles = [f.title for f in found]
    assert "chela-measure mod: not installed" in titles
    assert "limits: 5h unknown · 7d unknown" in titles
    assert "no session has a fresh statusLine or mod snapshot" in titles


def test_doctor_warns_when_the_mod_writes_to_another_chela_dir(tmp_path):
    mod = {"dir": tmp_path, "version": "0.1.0", "chela_dir": "/elsewhere"}
    found = measure_mod.report(Path("/here"), _state(tmp_path, mod=mod))
    warns = [f.title for f in found if f.level == WARN]
    assert warns == ["chela-measure writes to /elsewhere, not this CHELA_DIR /here"]


def test_doctor_warns_when_the_installed_mod_is_older_than_the_package(tmp_path):
    mod = {"dir": tmp_path, "version": "0.0.9", "chela_dir": "/here"}
    found = measure_mod.report(Path("/here"), _state(tmp_path, mod=mod))
    assert [f.title for f in found if f.level == WARN] == [
        "chela-measure installed is 0.0.9, this chela ships 0.1.0"]


def test_doctor_names_the_source_of_each_limit_and_session(tmp_path):
    lim = {"five_hour": {"used_pct": 23.5, "source": "mod"},
           "seven_day": {"used_pct": 7.0, "source": "statusline"}}
    sessions = {"aaaaaaaa-1": {"mod": 5.0, "statusline": 60.0}, "bbbbbbbb-2": {"statusline": 3.0}}
    mod = {"dir": tmp_path, "version": "0.1.0", "chela_dir": "/here"}
    found = measure_mod.report(Path("/here"), _state(tmp_path, mod=mod, limits=lim,
                                                      sessions=sessions, statusline=True))
    by_title = {f.title: f for f in found}
    assert all(f.level == OK for f in found)
    assert "limits: 5h 23.5% (mod) · 7d 7% (statusline)" in by_title
    assert "statusLine hook: wired in user settings" in by_title
    fed = by_title["2 session(s) fed: 1 by the mod, 1 by the statusLine"]
    assert fed.detail == "- aaaaaaaa: mod (both fresh)\n    - bbbbbbbb: statusline"


def test_the_doctor_fact_reads_this_chelas_files_end_to_end(tmp_path, monkeypatch):
    """The ``usage.sources`` fact from real files: nothing here builds its ``state`` by hand,
    so a half of ``read_state`` that reads the wrong place shows in the printed lines. The
    path is spelled from the directory, as measure.ts writes it."""
    chela_dir, claude = tmp_path / "cd", tmp_path / "claude"
    monkeypatch.setattr(config, "CHELA_DIR", chela_dir)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    _write(chela_dir / "context" / "mod" / f"{SID}.json", MEASURE["file"], age=1)
    # Two sessions, so the session count cannot be a constant and both producers are named.
    _write(chela_dir / "context" / "by-session" / "bbbbbbbb-2.json", {}, age=1)
    measure_mod.install(claude / "skills" / "chela-measure", chela_dir)
    _write(claude / "settings.json", {"statusLine": {"command": "/h/cache-statusline.sh"}})

    fact = next(f for f in runtime_truth.facts() if f.name == "usage.sources")
    found = fact.report(fact.declare(), fact.read_back())
    assert [(f.level, f.title) for f in found] == [
        (OK, f"chela-measure mod 0.1.0 installed at {claude / 'skills' / 'chela-measure'}"),
        (OK, "statusLine hook: wired in user settings"),
        (OK, "limits: 5h 23.5% (mod, 0 min ago) · 7d 7% (mod, 0 min ago)"),
        (OK, "2 session(s) fed: 1 by the mod, 1 by the statusLine"),
    ]
    assert found[-1].detail == "- 0f0f0f0f: mod\n    - bbbbbbbb: statusline"


def _doctor_lines():
    """``chela doctor``'s usage.sources output from the files on disk, every field of it."""
    fact = next(f for f in runtime_truth.facts() if f.name == "usage.sources")
    return [(f.level, f.title, f.detail) for f in fact.report(fact.declare(), fact.read_back())]


def test_the_doctor_fact_end_to_end_when_every_answer_differs(tmp_path, monkeypatch):
    """🔴 GUARD: the same fact in a world where EVERY answer is the opposite of the healthy
    test's: an old copy recording another dir, someone else's status line, a stale session, a
    session with both files. A gatherer half that is a constant, or is derived from another
    half (the package's version read from the installed copy), matches one world, never both."""
    chela_dir, claude = tmp_path / "cd", tmp_path / "claude"
    monkeypatch.setattr(config, "CHELA_DIR", chela_dir)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setattr(config, "cache_stale_seconds", lambda: 7200)
    dest = claude / "skills" / "chela-measure"
    measure_mod.install(dest, tmp_path / "elsewhere")
    (dest / ".claude-plugin" / "plugin.json").write_text('{"name": "chela-measure", "version": "0.0.9"}')
    _write(claude / "settings.json", {"statusLine": {"command": "/usr/bin/my-own-line"}})
    cache = chela_dir / "context"
    _write(cache / "mod" / "cccccccc-stale.json", MEASURE["file"], age=3 * 3600 + 5)
    _write(cache / "mod" / f"{SID}.json", {}, age=60)
    _write(cache / "by-session" / f"{SID}.json",
           {"rate_limits": {"seven_day": {"used_percentage": 9, "resets_at": 1}}}, age=5)
    assert _doctor_lines() == [
        (OK, f"chela-measure mod 0.0.9 installed at {dest}", ""),
        (WARN, f"chela-measure writes to {tmp_path / 'elsewhere'}, not this CHELA_DIR {chela_dir}",
         "A session without $CHELA_DIR feeds another chela. Re-run `chela install-mod`."),
        (WARN, "chela-measure installed is 0.0.9, this chela ships 0.1.0",
         "Re-run `chela install-mod`."),
        (OK, "statusLine hook: not in user settings", ""),
        (OK, "limits: 5h 23.5% (mod, 180 min ago) · 7d 9% (statusline, 0 min ago)", ""),
        (OK, "1 session(s) fed: 0 by the mod, 1 by the statusLine",
         "- 0f0f0f0f: statusline (both fresh)"),
    ]


def test_the_doctor_fact_end_to_end_with_nothing_there(tmp_path, monkeypatch):
    """🔴 GUARD (acceptance): no mod, no statusLine, no files. Unknown with the reason, never 0%."""
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "cd")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    why = "no statusLine or mod rate_limits found"
    assert _doctor_lines() == [
        (OK, "chela-measure mod: not installed",
         "`chela install-mod` feeds the 5h/7d limits, context and cost from "
         "Claude Code's session.measure hook, with no statusLine."),
        (OK, "statusLine hook: not in user settings", ""),
        (OK, "limits: 5h unknown · 7d unknown", f"five_hour: {why}; seven_day: {why}"),
        (OK, "no session has a fresh statusLine or mod snapshot", ""),
    ]


def test_the_doctor_fact_says_unrecorded_for_a_copy_with_no_chela_json(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "cd")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    dest = measure_mod.install(tmp_path / "claude" / "skills" / "chela-measure", tmp_path / "cd")
    (dest / "chela.json").unlink()
    assert [t for lvl, t, _ in _doctor_lines() if lvl == WARN] == [
        f"chela-measure writes to (unrecorded), not this CHELA_DIR {tmp_path / 'cd'}"]


def test_an_unreadable_usage_owner_is_a_warning_never_fatal(monkeypatch):
    def boom():
        raise OSError("denied")
    monkeypatch.setattr(measure_mod, "read_state", boom)
    fact = next(f for f in runtime_truth.facts() if f.name == "usage.sources")
    found = runtime_truth.audit(fact)
    assert [f.level for f in found] == [WARN]
    assert found[0].title.startswith("CANNOT VERIFY usage.sources")


def test_a_real_zero_percent_is_printed_as_zero_not_unknown(tmp_path):
    """A limit that truly reads 0% is a figure; only a missing one is unknown."""
    lim = {"five_hour": {"used_pct": 0.0, "source": "mod", "age_s": 119.9, "reason": None},
           "seven_day": {"used_pct": None, "source": None, "age_s": None, "reason": "why"}}
    line = next(f for f in measure_mod.report(tmp_path, _state(tmp_path, limits=lim))
                if f.title.startswith("limits:"))
    assert (line.title, line.detail) == ("limits: 5h 0% (mod, 1 min ago) · 7d unknown",
                                         "seven_day: why")


def test_the_cli_says_where_the_mod_writes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "cd")
    monkeypatch.setattr("sys.argv", ["chela", "install-mod", "--dir", str(tmp_path / "d")])
    main.main()
    assert f"it writes to {tmp_path}/cd/context/mod/<session>.json" in capsys.readouterr().out


def test_doctor_lists_at_most_twenty_sessions_and_counts_them_all(tmp_path):
    sessions = {f"{i:08d}-x": {"mod": 1.0} for i in range(21)}
    last = measure_mod.report(tmp_path, _state(tmp_path, sessions=sessions))[-1]
    assert last.title == "21 session(s) fed: 21 by the mod, 0 by the statusLine"
    assert last.detail.split("\n") == ["- 00000000: mod"] + [
        f"    - {i:08d}: mod" for i in range(1, 20)]


@pytest.mark.parametrize("age, fresh", [(600, True), (601, False)])
def test_a_session_is_fresh_up_to_and_including_the_cutoff(ctx, age, fresh):
    f = _mod(ctx)
    os.utime(f, (1_000_000, 1_000_000))
    got = measure_mod.session_sources(ctx, now=1_000_000 + age, stale_s=600)
    assert got == ({SID: {"mod": float(age)}} if fresh else {})


def test_the_config_dir_expands_a_tilde(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/cfg")
    assert measure_mod.claude_config_dir() == Path.home() / "cfg"


@pytest.mark.parametrize("settings", ["[1]", '{"statusLine": {"command": 5}}', '{"statusLine": {}}'])
def test_statusline_wired_survives_odd_settings(tmp_path, settings):
    (tmp_path / "settings.json").write_text(settings)
    assert measure_mod.statusline_wired(tmp_path / "settings.json") is False


def test_install_ignores_a_cache_folder_and_a_crashed_runs_leftovers(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(MOD, src)
    (src / "hooks" / "__pycache__").mkdir()
    (src / "hooks" / "__pycache__" / "x.pyc").write_text("x")
    crashed = tmp_path / f".chela-measure.{os.getpid()}.tmp"
    crashed.mkdir()
    (crashed / "half.ts").write_text("half")
    dest = measure_mod.install(tmp_path / "chela-measure", tmp_path / "cd", src=src)
    assert not crashed.exists() and not (dest / "half.ts").exists()
    assert not list(dest.rglob("__pycache__"))


def test_the_cli_remove_refuses_a_foreign_folder(tmp_path, monkeypatch, capsys):
    dest = tmp_path / "chela-measure"
    dest.mkdir()
    monkeypatch.setattr("sys.argv", ["chela", "install-mod", "--dir", str(dest), "--remove"])
    with pytest.raises(SystemExit) as e:
        main.main()
    assert e.value.code == 1 and dest.exists()
    assert "not removing it" in capsys.readouterr().err


def test_newest_limits_names_the_producer_and_age_at_any_age(ctx):
    _write(ctx / "mod" / f"{SID}.json", MEASURE["file"], age=3 * 3600)
    _write(ctx / "by-session" / f"{SID}.json",
           {"rate_limits": {"seven_day": {"used_percentage": 9, "resets_at": 1}}}, age=60)
    lim = measure_mod.newest_limits(ctx, now=time.time())
    assert (lim["five_hour"]["used_pct"], lim["five_hour"]["source"]) == (23.5, "mod")
    assert 3 * 3600 - 5 < lim["five_hour"]["age_s"] < 3 * 3600 + 60
    assert (lim["seven_day"]["used_pct"], lim["seven_day"]["source"]) == (9, "statusline")
    assert measure_mod._limit_text(lim["five_hour"]) == "23.5% (mod, 180 min ago)"


@pytest.mark.parametrize("bad", [[1, 2], "five_hour", 5, None])
def test_a_file_whose_rate_limits_is_not_an_object_is_skipped(ctx, bad):
    _write(ctx / "mod" / "aaaaaaaa-bad.json", {"rate_limits": bad}, age=0)
    _write(ctx / "by-session" / f"{SID}.json", MEASURE["file"], age=60)
    best = context.freshest_limits(ctx)
    assert (best["five_hour"][1], best["five_hour"][3]) == (23.5, "statusline")


def test_newest_limits_with_no_producer_is_unknown(ctx):
    lim = measure_mod.newest_limits(ctx)
    assert lim["seven_day"] == {"used_pct": None, "source": None, "age_s": None,
                                "reason": "no statusLine or mod rate_limits found"}
    assert measure_mod._limit_text(lim["seven_day"]) == "unknown"


def test_installed_state_reads_the_copy(tmp_path):
    dest = tmp_path / "chela-measure"
    assert measure_mod.installed_state(dest) is None
    measure_mod.install(dest, tmp_path / "cd")
    assert measure_mod.installed_state(dest) == {
        "dir": dest, "version": measure_mod.packaged_version(), "chela_dir": str(tmp_path / "cd")}
    (dest / "chela.json").write_text("garbage")
    assert measure_mod.installed_state(dest)["chela_dir"] is None
    (dest / "chela.json").write_text('{"chela_dir": 5}')
    assert measure_mod.installed_state(dest)["chela_dir"] is None


def test_the_packaged_version_is_the_manifests():
    assert measure_mod.packaged_version() == "0.1.0"


# --- the mod's own suite, against the engine ----------------------------------------------

@functools.cache
def _claude_with_mods() -> str | None:
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", out.stdout)
    return exe if m and tuple(map(int, m.groups())) >= (2, 1, 301) else None


@pytest.mark.skipif(_claude_with_mods() is None, reason="needs Claude Code >= 2.1.301")
def test_the_mods_own_suite_passes():
    """``measure.test.ts`` against the engine: the recorded payload, the chela-dir order,
    the unsafe ids, and two mods both receiving session.measure. CI has no Claude Code."""
    proc = subprocess.run([_claude_with_mods(), "plugin", "test", str(MOD)],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    assert re.search(r"\b0 fail\b", proc.stdout + proc.stderr)
