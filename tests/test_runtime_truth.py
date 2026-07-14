"""The registry's own fence: **a check that has never been seen to go red is not a check.**

``chela doctor`` is now generated from :mod:`chela.runtime_truth` — for every fact, read
what chela DECLARES, read back what its OWNER really has, compare. That kills the private
blind spot every hand-written check acquires (CMX-63's drift check compared only the first
hook; CMX-65's test wrapper named one ``.mjs`` file), but it introduces a new artifact —
the registry — and an artifact can drift from reality exactly like the ones it replaced.

So the registry is held to the bar it exists to enforce:

* :func:`test_every_fact_has_a_red_test` — every entry in ``runtime_truth.facts()`` must
  appear in :data:`CORRUPTIONS` below. Add a fact without a way to break it and this fails.
* :func:`test_corrupting_the_owned_value_makes_doctor_say_so` — each corruption BREAKS THE
  OWNED VALUE (the copy that governs behaviour, not chela's copy of it) and asserts doctor
  reports it, **naming the fact**. Not "the check passes" — "I broke it and the gate went
  red".
* :func:`test_a_new_fact_needs_no_new_doctor_code` — the whole point of the registry.
* :func:`test_an_owner_that_cannot_be_read_is_never_green` — a doctor that goes green
  because it could not look is the bug, one level up.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from chela import (
    capabilities,
    config,
    discovery,
    dispatcher,
    doctor,
    epoch,
    hooks,
    inbox,
    main,
    runtime_truth,
    sessions,
    transcripts,
)
from chela.telegram import bindings

PORT = 5005
SESSION = "7f3a91c2-4b8e-4d15-9c62-1e0d5a8b3f47"
EPOCH = "786-1784045825"          # the tmux server that issued every `@N` in this fleet


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A whole chela install that is HEALTHY — every fact agrees with its owner.

    Every corruption below starts from this and breaks exactly one owned value, so a red
    finding can only have come from that break.
    """
    chela_dir = tmp_path / "chela"
    (chela_dir / "plugin").mkdir(parents=True)
    monkeypatch.setattr(config, "CHELA_DIR", chela_dir)
    monkeypatch.setenv("CHELA_DIR", str(chela_dir))

    claude = tmp_path / "claude"
    (claude / "plugins").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))

    # the env file, and a process environment that agrees with it
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text(
        "---\nproject_key: CMX\ntracker:\n  kind: markdown\n  path: TODO.md\n---\ndo it\n")
    (repo / "TODO.md").write_text("- [ ] a task\n")
    (chela_dir / "chela.env").write_text(
        f"CHELA_DASHBOARD_PORT={PORT}\nCHELA_TMUX_SESSION=chela\n")
    monkeypatch.setenv("CHELA_ENV_FILE", str(chela_dir / "chela.env"))
    monkeypatch.setenv("CHELA_DASHBOARD_PORT", str(PORT))
    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")

    # the dashboard really bound PORT; a daemon really came up dispatching the workflow
    config.publish_dashboard_port(PORT)
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [repo / "WORKFLOW.md"])
    capabilities.publish(capabilities.effective())

    # the plugin: rendered, and INSTALLED where an agent would load it
    hooks.render_plugin(chela_dir / "plugin", port=PORT)
    install_plugin(hooks.hooks_spec(PORT))

    # tmux: the session exists and no run claims a dead window. tmux is an OWNER, so the
    # suite hands the code a window table instead of asking the developer's real fleet — and
    # an EPOCH, because `@1` only means anything inside the server that issued it (CMX-77).
    # The in-flight run was spawned under that same server, so its recorded id still names
    # its agent.
    monkeypatch.setattr(discovery, "session_exists", lambda *a, **k: True)
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@1": "cmx-66"})
    monkeypatch.setattr(epoch, "current", lambda: EPOCH)
    monkeypatch.setattr(runtime_truth, "_in_flight_runs",
                        lambda: {"CMX-66": {"wid": "@1", "epoch": EPOCH}})

    # the decisions inbox: the orchestrator registered @1 under the server that is running,
    # so the address it would push to is the window it thinks it is.
    monkeypatch.setenv("CHELA_INBOX_FILE", str(chela_dir / "inbox.json"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    inbox.save({"orchestrator": "@1", "orchestrator_epoch": EPOCH,
                "orchestrator_name": "cmx-66", "watches": {}, "queue": [], "runs_seen": {}})

    # the rework loop: a run is parked in changes_requested, and git still has the branch
    # it must be re-spawned into. git is the OWNER here, for the same reason tmux is above.
    # (`needs_human`: parked and waiting on a PERSON, so no tick is owed it — a
    # `changes_requested` run IS owed one, and _stalled_report checks that separately.)
    monkeypatch.setattr(runtime_truth, "_parked_runs", lambda: {
        "CMX-68": {"branch": "cmx-68", "worktree": str(tmp_path / "wt"), "repo": str(repo),
                   "workflow": str(repo / "WORKFLOW.md"), "status": "needs_human",
                   "waiting": 30.0},
    })
    monkeypatch.setattr(runtime_truth, "_git_branches", lambda repo: {"dev", "cmx-68"})

    # the checks: a PR is under review and GitHub says it is GREEN. GitHub is the OWNER
    # here for the same reason tmux and git are above — the suite hands the code an answer
    # rather than the network, and the corruption below changes THAT answer, not our copy.
    monkeypatch.setattr(runtime_truth, "_reviewed_prs", lambda: {
        "CMX-69": {"pr": "https://github.com/acme/repo/pull/99", "repo": str(repo),
                   "checks": "passing"},
    })
    monkeypatch.setattr(runtime_truth, "_gh_pr_checks",
                        lambda pr, repo: dispatcher.CIStatus(dispatcher.CI_PASSING, "abc123"))

    # the collector: it executes every .test.mjs on disk
    monkeypatch.setattr(
        runtime_truth, "collected_js_suites",
        lambda root: runtime_truth.observed(set(runtime_truth._js_suites_on_disk())))
    monkeypatch.setattr(runtime_truth, "_collector_applies", lambda: True)

    # the relay: @1 is bound to a Telegram topic, and the agent in it is really writing the
    # transcript chela would relay. The OWNER here is that file on disk — the corruption
    # below deletes it, which is the 2026-07-14 outage exactly (a bound window whose
    # transcript cannot be found relays NOTHING, in silence).
    projects = tmp_path / "projects"
    agent_cwd = str(tmp_path / "agent")
    (projects / transcripts.encode_cwd(agent_cwd)).mkdir(parents=True)
    (projects / transcripts.encode_cwd(agent_cwd) / f"{SESSION}.jsonl").write_text(
        '{"type":"assistant","timestamp":"2026-07-14T12:00:00Z"}\n')
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", projects)
    monkeypatch.setattr(sessions, "panes", lambda force=False: {"@1": sessions.Pane(
        wid="@1", path=agent_cwd, command="claude", claude_pid=1, launched_in=agent_cwd)})
    registry = bindings.BindingRegistry(chat_id="-100")
    registry.bind("@1", 42)
    registry.set_topic_name("@1", "cmx-66")
    registry.save(bindings.default_bindings_path())
    return tmp_path


def install_plugin(spec: dict, version: str = "0.1.0") -> Path:
    """A plugin copy where `/plugin install` puts one — the manifest agents really load."""
    root = hooks.plugins_dir() / "cache" / "chela" / "chela" / version
    (root / "hooks").mkdir(parents=True, exist_ok=True)
    (root / "hooks" / "hooks.json").write_text(json.dumps(spec), encoding="utf-8")
    (hooks.plugins_dir() / "installed_plugins.json").write_text(json.dumps({
        "version": 2,
        "plugins": {"chela@chela": [{"scope": "user", "installPath": str(root),
                                     "version": version}]},
    }), encoding="utf-8")
    return root


def _red(findings) -> list[doctor.Finding]:
    return [f for f in findings if f.level != doctor.OK]


# --- the corruptions: one per fact, each breaking the OWNED copy ----------------------
#
# Every one of these breaks the copy that GOVERNS BEHAVIOUR — the file processes source,
# the environment they really carry, tmux's answer, the socket that is really bound, the
# manifest agents really load, the daemon that is really running, the collector's real
# list. Not chela's copy of it: that is the whole distinction the registry exists to draw,
# and a corruption that edited our own copy would prove nothing.


def _break_env_file(tmp_path, monkeypatch):
    """The file relocates itself — so it is found via a CHELA_DIR it then contradicts."""
    (config.CHELA_DIR / "chela.env").write_text("CHELA_DIR=/somewhere/else\n")
    return doctor.ERROR


def _break_env_running(tmp_path, monkeypatch):
    """`pm2 restart --update-env` MERGES: the process still carries the old value."""
    monkeypatch.setenv("CHELA_TMUX_SESSION", "ccbot")     # the file still says `chela`
    return doctor.WARN


def _break_tmux_session(tmp_path, monkeypatch):
    """tmux has no such session — every window lookup in the fleet resolves to nothing."""
    monkeypatch.setattr(discovery, "session_exists", lambda *a, **k: False)
    return doctor.WARN


def _break_dashboard_port(tmp_path, monkeypatch):
    """The dashboard BOUND a port the config does not know about (`--port` wins). CMX-41."""
    config.publish_dashboard_port(6001)
    return doctor.ERROR


def _break_plugin_rendered(tmp_path, monkeypatch):
    """The rendered manifest is stale — and it is what a reinstall COPIES FORWARD."""
    stale = hooks.hooks_spec(PORT)
    stale["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"] = 2
    hooks._write_json(config.CHELA_DIR / "plugin" / "hooks" / "hooks.json", stale)
    return doctor.ERROR


def _break_plugin_installed(tmp_path, monkeypatch):
    """CMX-56, verbatim: the INSTALLED copy still kills the gate hook after 2 seconds."""
    stale = hooks.hooks_spec(PORT)
    stale["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"] = 2
    install_plugin(stale)
    return doctor.ERROR


def _break_daemon_capabilities(tmp_path, monkeypatch):
    """The RUNNING daemon came up with dispatch OFF; the env file has since been fixed.
    Both copies of the config now say ON — and nothing is dispatching. CMX-53."""
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [])
    capabilities.publish(capabilities.effective())        # the daemon that is really up
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [tmp_path / "repo" / "WORKFLOW.md"])
    return doctor.ERROR


def _break_dispatch_workflows(tmp_path, monkeypatch):
    """The tracker the dispatcher reads its queue from is gone. No queue, and no word."""
    (tmp_path / "repo" / "TODO.md").unlink()
    return doctor.ERROR


def _break_dispatch_hold(tmp_path, monkeypatch):
    """A held queue claims NOTHING — and looks exactly like a quiet day."""
    from chela import hold

    hold.take(reason="rewriting the queue", ttl_seconds=600, by="@0")
    return doctor.WARN


def _break_tmux_windows(tmp_path, monkeypatch):
    """The run row says the agent is working in @1; tmux says @1 does not exist (CMX-62)."""
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@9": "something-else"})
    return doctor.WARN


def _break_inbox_address(tmp_path, monkeypatch):
    """The 2026-07-14 outage, verbatim: tmux was OOM-killed and came back RENUMBERED.

    The inbox is still addressed to the `@1` the orchestrator registered — but that id was
    issued by a server that is dead, and the one running now has given `@1` to somebody else.
    Five run_review events queued behind exactly this and NONE were delivered: no error, no
    log line, and this doctor green 14/14. The address is chela's copy; tmux owns which
    server is issuing ids, so that is what the corruption changes.
    """
    store = inbox.load()
    store["queue"] = [inbox._event("run_review", "📥 cmx-76 awaiting review", {"task_id": "T1"})]
    inbox.save(store)
    monkeypatch.setattr(epoch, "current", lambda: "9001-1784099999")   # a NEW tmux server
    return doctor.ERROR


def _break_tests_js_suites(tmp_path, monkeypatch):
    """The collector executes every JS suite but one — which is CMX-65, exactly: a test
    that exists and is never run, while `pytest -q` reports green."""
    suites = runtime_truth._js_suites_on_disk()
    assert suites, "the repo has no *.test.mjs — this fact would be checking nothing"
    monkeypatch.setattr(
        runtime_truth, "collected_js_suites",
        lambda root: runtime_truth.observed(set(suites[1:])))
    return doctor.ERROR


def _break_runs_parked_branch(tmp_path, monkeypatch):
    """The branch a sent-back run has to be re-spawned INTO is gone (CMX-68).

    The run row still says `changes_requested`, the PR is still open, and the work the PR
    points at is now unreachable — the rework loop can never turn again for this run, and
    without this check nothing would say so until the dispatcher tried and failed.
    """
    monkeypatch.setattr(runtime_truth, "_git_branches", lambda repo: {"dev"})
    return doctor.ERROR


def _break_pr_checks(tmp_path, monkeypatch):
    """CI is RED on a PR that is sitting in `awaiting_review`, one click from the base branch.

    This is PR #80, verbatim: the run row said the work was done, the reviewer read the
    code, and the artifact that governs whether it can ship said FAILURE. Nobody asked it,
    it was merged, and `dev` broke (hotfix 23664e2). Doctor asks it.
    """
    monkeypatch.setattr(
        runtime_truth, "_gh_pr_checks",
        lambda pr, repo: dispatcher.CIStatus(
            dispatcher.CI_FAILING, "abc123", ("test (3.11)",), ("42",)))
    return doctor.ERROR


def _unreadable_pr_checks(tmp_path, monkeypatch):
    """The other half of the fact, and the one that re-opens the hole if it is got wrong:
    an owner that cannot be READ is CANNOT VERIFY, never a silent pass."""
    monkeypatch.setattr(
        runtime_truth, "_gh_pr_checks",
        lambda pr, repo: dispatcher.CIStatus(dispatcher.CI_UNKNOWN, detail="gh is not installed"))
    return doctor.ERROR


def _break_relay_transcripts(tmp_path, monkeypatch):
    """The bound window's transcript is not where chela looks — the 2026-07-14 outage.

    Live, the cause was a `claude --resume` whose transcript stayed in the project dir the
    session was BORN in while the monitor searched the pane's cwd; here it is simply gone.
    Either way the window is bound to a topic and relays NOTHING, and every other surface
    stays green: the binding reconciles, the topic exists, inbound still works. The relay
    was dead for an hour and the only thing that ever noticed was a human wondering why the
    agent had gone quiet.
    """
    for path in (tmp_path / "projects").rglob("*.jsonl"):
        path.unlink()
    return doctor.ERROR


CORRUPTIONS = {
    "relay.transcripts": _break_relay_transcripts,
    "env.file": _break_env_file,
    "env.running": _break_env_running,
    "tmux.session": _break_tmux_session,
    "dashboard.port": _break_dashboard_port,
    "plugin.rendered": _break_plugin_rendered,
    "plugin.installed": _break_plugin_installed,
    "daemon.capabilities": _break_daemon_capabilities,
    "dispatch.workflows": _break_dispatch_workflows,
    "dispatch.hold": _break_dispatch_hold,
    "tmux.windows": _break_tmux_windows,
    "inbox.address": _break_inbox_address,
    "runs.parked_branch": _break_runs_parked_branch,
    "pr.checks": _break_pr_checks,
    "tests.js_suites": _break_tests_js_suites,
}


def test_every_fact_has_a_red_test():
    """The registry is an artifact, and an artifact drifts. This is how it is kept honest:
    a fact with no way to break it is a fact nobody has ever seen fail."""
    registered = {f.name for f in runtime_truth.facts()}
    assert registered == set(CORRUPTIONS), (
        "every registry entry needs a corruption that proves its check can go red — "
        f"missing: {sorted(registered - set(CORRUPTIONS))}; "
        f"stale: {sorted(set(CORRUPTIONS) - registered)}"
    )


@pytest.mark.parametrize("name", sorted(CORRUPTIONS))
def test_corrupting_the_owned_value_makes_doctor_say_so(name, fleet, monkeypatch):
    """Break what the OWNER has, and the gate goes red — naming the fact."""
    assert not _red([f for f in doctor.check() if f.fact == name]), (
        f"{name} is not green before the corruption — the fixture is lying, not the check")

    level = CORRUPTIONS[name](fleet, monkeypatch)
    reported = [f for f in doctor.check() if f.fact == name and f.level == level]
    assert reported, (
        f"corrupting the value {name} REALLY runs on did not make doctor report it at "
        f"{level}. A check that cannot be seen to go red is not a check.")


def test_a_check_state_that_cannot_be_read_is_never_a_pass(fleet, monkeypatch):
    """⛔ `gh` missing / offline / rate-limited = UNKNOWN, not GREEN.

    The corruption above proves doctor sees a RED CI. This one proves it sees an UNASKED
    one — which is the failure mode that actually shipped: nothing in chela knew a check
    existed, so every PR was, in effect, "unread", and unread was silently treated as fine.
    """
    _unreadable_pr_checks(fleet, monkeypatch)
    findings = [f for f in doctor.check() if f.fact == "pr.checks"]
    assert findings, "an unreadable check state produced no finding at all"
    assert all(f.level == doctor.ERROR for f in findings)
    assert "CANNOT VERIFY pr.checks" in findings[0].title
    assert "gh is not installed" in findings[0].detail


def test_a_healthy_fleet_is_green(fleet):
    """The other half of the bar: doctor must not cry wolf, or nobody reads it."""
    findings = doctor.check()
    assert not [f for f in findings if f.level == doctor.ERROR], "\n".join(
        f.render() for f in _red(findings))
    assert {f.fact for f in findings} >= {"dashboard.port", "plugin.installed",
                                          "daemon.capabilities", "tmux.session"}


# --- the mechanism itself -------------------------------------------------------------

def test_a_new_fact_needs_no_new_doctor_code(fleet, monkeypatch):
    """Register a fact; doctor checks it. That is the entire point: the seven bespoke
    checks are gone, and with them the eighth one nobody would have written."""
    invented = runtime_truth.Fact(
        name="invented.fact",
        declared_by="this test",
        owned_by="a thing that disagrees",
        declare=lambda: "we-say-this",
        read_back=lambda: runtime_truth.observed("the-owner-says-otherwise"),
        report=lambda declared, obs: [
            runtime_truth.Finding(doctor.ERROR, f"{declared} != {obs.value}")],
    )
    monkeypatch.setattr(runtime_truth, "facts", lambda: [invented])

    findings = doctor.check()
    assert [f.fact for f in findings] == ["invented.fact"]
    assert findings[0].level == doctor.ERROR


def test_an_owner_that_cannot_be_read_is_never_green(fleet, monkeypatch):
    """The read-back CAN fail — the daemon is down, tmux is gone, the plugin cache moved
    between Claude Code releases. That is "cannot verify", loudly. Never a silent pass, and
    never a crash."""
    def owner_is_gone():
        raise OSError("the plugin cache moved between releases")

    exploded = runtime_truth.Fact(
        name="unreadable.fact",
        declared_by="an env file",
        owned_by="an owner that has stopped answering",
        declare=lambda: "whatever",
        read_back=owner_is_gone,
        report=lambda declared, obs: [runtime_truth.Finding(doctor.OK, "green!")],
    )
    monkeypatch.setattr(runtime_truth, "facts", lambda: [exploded])

    findings = doctor.check()
    assert [f.level for f in findings] == [doctor.ERROR]
    assert "CANNOT VERIFY unreadable.fact" in findings[0].title
    assert "the plugin cache moved between releases" in findings[0].detail
    assert "green!" not in findings[0].title            # the fact's own report never ran


def test_a_read_back_that_explodes_does_not_crash_doctor(fleet, monkeypatch):
    """Doctor is run by an operator mid-incident and by the dispatcher. It reports; it
    does not traceback."""
    monkeypatch.setattr(runtime_truth, "_installed_read",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    findings = doctor.check()
    assert any(f.fact == "plugin.installed" and f.level == doctor.ERROR for f in findings)


# --- exit codes: the dispatcher and the operator both read them -----------------------

def test_exit_code_is_1_on_an_error(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "check",
                        lambda: [doctor.Finding(doctor.ERROR, "broken", "detail")])
    with pytest.raises(SystemExit) as exc:
        main.cmd_doctor(object())
    assert exc.value.code == 1
    assert "broken" in capsys.readouterr().out


def test_exit_code_is_0_on_warnings_alone(monkeypatch, capsys):
    """A WARN is a thing worth knowing, not a thing that is broken — dispatch being off is
    a choice, a held queue is deliberate. Exiting 1 on those would train the operator to
    ignore the exit code, and then the ERRORs go unread too."""
    monkeypatch.setattr(doctor, "check", lambda: [
        doctor.Finding(doctor.WARN, "worth knowing"),
        doctor.Finding(doctor.OK, "fine"),
    ])
    main.cmd_doctor(object())                            # no SystemExit
    assert "worth knowing" in capsys.readouterr().out


# --- the fact whose owner is the pytest collector (CMX-65) ---------------------------

def test_the_collector_fact_asks_pytest_not_our_own_glob(fleet, monkeypatch):
    """`tests/test_js_suites.py` globs the repo; the collector decides what RUNS. The two
    disagreed for a day, so the fact is read back from the collector — and doctor must not
    fall back to the glob when it cannot ask."""
    calls: list[Path] = []

    def collector(root):
        calls.append(root)
        return runtime_truth.observed(set(runtime_truth._js_suites_on_disk()))

    monkeypatch.setattr(runtime_truth, "collected_js_suites", collector)
    findings = [f for f in doctor.check() if f.fact == "tests.js_suites"]
    assert calls, "the collector was never asked"
    assert findings and findings[0].level == doctor.OK


def test_the_collector_fact_is_not_audited_from_inside_the_suite():
    """It shells `pytest --collect-only`: audited unguarded, the suite would collect
    itself, from itself, once per doctor run. It is exercised above with the collector
    handed in — which is also the only way to break it on purpose."""
    assert os.environ.get("PYTEST_CURRENT_TEST")
    assert not runtime_truth.fact("tests.js_suites").applies()


def test_the_registry_only_applies_facts_that_are_facts_of_this_install(tmp_path,
                                                                        monkeypatch):
    """A machine with no plugin rendered and none installed is not running hooks: there is
    no fact there to check. (It is not a silent pass — there is nothing to pass.)"""
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "empty")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    assert not runtime_truth.fact("plugin.installed").applies()


# --- what the old bespoke checks used to do, still done -------------------------------

def test_the_hold_is_read_from_the_file_not_from_the_daemons_snapshot(fleet):
    """A hold taken AFTER the daemon booted is not in daemon.json. The file is the shared
    truth precisely because the two live in different processes."""
    from chela import hold

    hold.take(reason="rewriting", ttl_seconds=600, by="@0")     # after publish()
    held = [f for f in doctor.check() if f.fact == "dispatch.hold"]
    assert held and "HELD" in held[0].title


def test_an_expired_hold_still_says_so(fleet):
    from chela import hold

    now = time.time()
    hold.path().write_text(json.dumps({
        "reason": "crashed mid-rewrite", "by": "@0", "pid": 1,
        "created_at": now - 7200, "expires_at": now - 3600,
    }))
    findings = [f for f in doctor.check() if f.fact == "dispatch.hold"]
    assert findings and "EXPIRED" in findings[0].title


# --- the OTHER half of "parked": is anything ever going to come for it? -----------------
#
# `runs.parked_branch` asked git one question — does the branch still exist? — and stopped
# there. But a run sent back for rework is a PROMISE ("the dispatcher re-spawns it on the
# next tick"), and three ordinary conditions break that promise in total silence: the
# workflow is not in CHELA_DISPATCH_WORKFLOWS, its WORKFLOW.md does not parse, or a hold was
# taken and forgotten. The run then sits in `changes_requested` FOREVER — branch intact, PR
# open, verdict written, and nobody coming. `changes_requested` only emits an event on the
# EDGE, so once it is parked, this is the only thing that speaks.

def _claim(**over) -> dict:
    claim = {"branch": "cmx-68", "worktree": "/wt", "repo": "/repo",
             "workflow": "/repo/WORKFLOW.md", "status": "changes_requested", "waiting": 60.0}
    claim.update(over)
    return {"CMX-68": claim}


def test_a_sent_back_run_whose_workflow_NOTHING_dispatches_is_an_ERROR(monkeypatch):
    """The daemon ticks the workflows in CHELA_DISPATCH_WORKFLOWS and no others. A run sent
    back for rework under a workflow that is not in that list will never be re-spawned by
    anything, ever."""
    monkeypatch.setattr(runtime_truth.config, "DISPATCH_WORKFLOWS", [Path("/other/WORKFLOW.md")])

    findings = runtime_truth._stalled_report(_claim())

    assert [f.level for f in findings] == [doctor.ERROR]
    assert "NOTHING dispatches" in findings[0].title
    assert "chela dispatch /repo/WORKFLOW.md" in findings[0].detail


def test_a_sent_back_run_that_has_waited_for_HOURS_is_a_WARN(monkeypatch):
    """Legitimate only while every slot is busy — a rework normally restarts on the next
    tick. Past an hour it is far likelier that a hold was forgotten or the daemon is down."""
    monkeypatch.setattr(runtime_truth.config, "DISPATCH_WORKFLOWS",
                        [Path("/repo/WORKFLOW.md")])

    fresh = runtime_truth._stalled_report(_claim(waiting=30.0))
    stale = runtime_truth._stalled_report(
        _claim(waiting=runtime_truth.PARKED_STALL_SECONDS + 1))

    assert fresh == []                                     # one tick old — that is the loop
    assert [f.level for f in stale] == [doctor.WARN]
    assert "has not been re-spawned" in stale[0].title
    assert "hold" in stale[0].detail                       # names all three causes


def test_a_run_parked_on_a_HUMAN_is_never_reported_as_stalled(monkeypatch):
    """`needs_human` is parked ON PURPOSE: the loop gave up and is waiting for a person. No
    tick is owed it, so no amount of waiting makes it a finding."""
    monkeypatch.setattr(runtime_truth.config, "DISPATCH_WORKFLOWS", [])

    assert runtime_truth._stalled_report(
        _claim(status="needs_human", waiting=99999.0)) == []
