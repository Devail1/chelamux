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
import socket
import subprocess
import sys
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
    event_log,
    hooks,
    inbox,
    main,
    messenger,
    runtime_truth,
    sessions,
    transcripts,
    update,
)
from chela.telegram import bindings

PORT = 5005
SESSION = "7f3a91c2-4b8e-4d15-9c62-1e0d5a8b3f47"
EPOCH = "786-1784045825"          # the tmux server that issued every `@N` in this fleet

# Captured at import time, before any test's `fleet` fixture monkeypatches the module
# attribute to `lambda: True` — the CMX-313 gate tests restore THIS, the real
# implementation, so they exercise the actual production code instead of a lambda that
# merely repeats its logic and would stay green under a mutation of the real one.
_REAL_PROCESS_NODE_IPC_ENV_APPLIES = runtime_truth._process_node_ipc_env_applies


@pytest.fixture
def fleet(tmp_path, monkeypatch, request):
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
    # agents.native_status_feed: `claude agents --json` answers fine — the suite stubs the
    # seam instead of shelling out for real (the real call costs up to 30s, see CMX-179).
    monkeypatch.setattr(runtime_truth, "_native_status_probe", lambda: (True, "0.8s"))
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
    # tmux.node_ipc_env: the global environment table carries no leaked Node IPC vars —
    # the state `dispatcher._new_window`'s scrub is supposed to leave behind after every
    # spawn. A real `tmux show-environment -g` call here would depend on the test host's
    # own tmux server, same reason session_exists/get_windows_by_id are stubbed above.
    monkeypatch.setattr(runtime_truth, "_tmux_global_env", lambda: {})
    # process.node_ipc_env: THIS test process carries no leaked Node IPC vars either —
    # the state a window not born under a poisoned tmux server actually has. Explicit,
    # not assumed: a leak here would make every other test in this file's baseline lie.
    monkeypatch.delenv("NODE_CHANNEL_FD", raising=False)
    monkeypatch.delenv("NODE_CHANNEL_SERIALIZATION_MODE", raising=False)
    # CMX-313: the fact only applies inside a tmux pane (see _process_node_ipc_env_applies)
    # — force it on here, same as _collector_applies above, so the fleet baseline exercises
    # it deterministically instead of depending on whether the suite happens to be run from
    # inside a real tmux pane.
    monkeypatch.setattr(runtime_truth, "_process_node_ipc_env_applies", lambda: True)
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

    # the dispatched workflow's preconditions: the resolved agent command's binary is on
    # PATH, gh is authenticated, and base_branch exists in git. Real PATH/gh/git lookups
    # are shelled out for the actual fact (an install with a broken PATH IS the bug it
    # catches) so the suite stubs the seam instead of depending on the dev box's PATH.
    monkeypatch.setattr(runtime_truth, "_agent_cmd_which", lambda binary: f"/usr/bin/{binary}")
    monkeypatch.setattr(runtime_truth, "_gh_auth_status", lambda: True)
    monkeypatch.setattr(runtime_truth, "_ref_exists", lambda repo, branch: True)
    # dispatch.base_write_remote: the repo has a remote to write the (CMX-174) isolated
    # tracker-strike / trial-ledger worktree through. `repo` here is a plain directory,
    # not a real git checkout — same reason the git-backed facts above are all stubbed.
    monkeypatch.setattr(runtime_truth, "_has_remote", lambda repo: True)

    # repo.upstream_synced: this checkout tracks its upstream cleanly — no local
    # divergence for `chela update` to have to recover from.
    monkeypatch.setattr(runtime_truth, "_upstream_synced_status",
                        lambda: update.UpdateStatus(ok=True, behind=0, ahead=0, branch="dev"))

    # repo.services_current: no chela-* PM2 service predates the checked-out code.
    monkeypatch.setattr(runtime_truth, "_services_current_status",
                        lambda: update.ServiceFreshness(ok=True, stale=[], commit_epoch=1000))

    # restore.dead_epoch_rows: nothing orphaned — every stamped row in this fleet
    # (the inbox registration above) matches the running epoch.
    monkeypatch.setattr(runtime_truth, "_restore_scan", lambda now: 0)

    # judge.blocked_race: no run in this fleet is stuck with a CAS-refused BLOCKING
    # verdict (CMX-239) — same reason _restore_scan is stubbed above: the real
    # `dispatcher.DB_PATH` is cached at import time against the developer's actual
    # ~/.chela, not this fixture's temp one.
    monkeypatch.setattr(runtime_truth, "_blocked_race_scan", lambda: {})

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

    # peer.transport: @1 was launched with --messaging-socket-path, and its Claude Code
    # session is really listening there — a live AF_UNIX socket, not just a file, since
    # the fact now PROBES reachability (CMX-224 rework: a file that merely exists can't
    # be told apart from a stale one nothing is listening on). The OWNER here is that
    # live listener, same reason tmux and git are owners above — the corruption below
    # deletes the file out from under it.
    peer_sock = messenger.deterministic_peer_socket_path("@1")
    peer_sock.parent.mkdir(parents=True, exist_ok=True)
    peer_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    peer_listener.bind(str(peer_sock))
    peer_listener.listen(1)
    request.addfinalizer(peer_listener.close)

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


def _break_tmux_node_ipc_env(tmp_path, monkeypatch):
    """CMX-252: a tmux server started under a node-parented ancestor (pm2 restart, a
    reboot) reintroduces the leaked IPC vars into the GLOBAL environment — the table
    `dispatcher._new_window`'s scrub cleared, but a NEW server never went through."""
    monkeypatch.setattr(runtime_truth, "_tmux_global_env",
                        lambda: {"NODE_CHANNEL_FD": "3",
                                 "NODE_CHANNEL_SERIALIZATION_MODE": "json"})
    return doctor.ERROR


def _break_process_node_ipc_env(tmp_path, monkeypatch):
    """CMX-281: THIS process — a window already alive, not a new spawn — carries the
    leaked vars in its OWN environment. `tmux.node_ipc_env`'s corruption (above) breaks
    the GLOBAL table a NEW window would inherit; this breaks what a window already
    running (like the one an agent is writing its PR from) actually has right now —
    exactly the state that fooled three agents into calling a red suite pre-existing."""
    monkeypatch.setenv("NODE_CHANNEL_FD", "3")
    monkeypatch.setenv("NODE_CHANNEL_SERIALIZATION_MODE", "json")
    return doctor.ERROR


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


def _break_unresolved_depends(tmp_path, monkeypatch):
    """CMX-234: a `depends:` marker whose title is a typo of the real bullet — it
    resolves to no task at all, open or closed, anywhere in the tracker. Before this
    fact existed the ONLY trace was a `dispatcher._ready` log.warning line."""
    (tmp_path / "repo" / "TODO.md").write_text(
        "- [ ] a task\n"
        '- [ ] follow-up task <!-- depends: "a tsak" -->\n'
    )
    return doctor.ERROR


def _break_agent_cmd(tmp_path, monkeypatch):
    """The resolved `agent.cmd` names a binary the spawning shell's PATH cannot find —
    tmux would type it into a fresh window and get `command not found` back."""
    monkeypatch.setattr(runtime_truth, "_agent_cmd_which", lambda binary: None)
    return doctor.ERROR


def _break_gh_auth(tmp_path, monkeypatch):
    """`gh auth status` says nobody is logged in — every dispatched agent's `gh pr
    create` fails on the last line of an otherwise-successful run."""
    monkeypatch.setattr(runtime_truth, "_gh_auth_status", lambda: False)
    return doctor.ERROR


def _break_base_branch(tmp_path, monkeypatch):
    """`workspace.base_branch` names a ref git does not have — `git worktree add`
    fails at the FIRST dispatch of this workflow, every time."""
    monkeypatch.setattr(runtime_truth, "_ref_exists", lambda repo, branch: False)
    return doctor.ERROR


def _break_base_write_remote(tmp_path, monkeypatch):
    """The repo has no `origin` remote — the isolated base-write worktree (CMX-174) can
    fetch and push nothing, so `_base_write_worktree` logs a WARNING and skips, every
    tick, forever: merged tasks keep rendering as open cards."""
    monkeypatch.setattr(runtime_truth, "_has_remote", lambda repo: False)
    return doctor.ERROR


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


def _break_hooks_flowing(tmp_path, monkeypatch):
    """A window's claude process is live and long past when `SessionStart` should have
    fired — but the event log has nothing from it. CMX-41, structurally: the manifest
    matches (plugin.rendered/plugin.installed are both green) and the hook still never
    arrived."""
    started = time.time() - 60.0
    agent_cwd = str(tmp_path / "agent")
    monkeypatch.setattr(sessions, "panes", lambda force=False: {"@1": sessions.Pane(
        wid="@1", path=agent_cwd, command="claude", claude_pid=1,
        launched_in=agent_cwd, started=started)})
    return doctor.ERROR


def _break_peer_transport(tmp_path, monkeypatch):
    """@1's peer-messaging socket is gone — an older Claude Code build, a window
    launched before --messaging-socket-path existed, or a socket that never bound.
    Every send_message/send_peer call to @1 now falls back to send_tmux SILENTLY,
    which is exactly the CMX-224 gap: chela doctor said nothing about it."""
    messenger.deterministic_peer_socket_path("@1").unlink()
    return doctor.WARN


def _break_hooks_attributed(tmp_path, monkeypatch):
    """A hook DID reach the log, but `wid_for_session` landed None — two agents sharing one
    cwd (CMX-190), or the window closed before the POST arrived. The record is the exact
    shape `chela/hooks.py:ingest` writes: `wid=None`, `session_id` kept."""
    event_log.append("hook.pre_tool_use", "Bash: ls", {}, wid=None,
                     session_id="1969180e-dead-beef-cafe-000000000000")
    return doctor.WARN


def _break_hooks_wid_rejected(tmp_path, monkeypatch):
    """A SessionStart's `X-Chela-Wid` named a window that was not live — a stale
    `$CHELA_WID` inherited from tmux's global environment, the actual CMX-192 root cause.
    The record is the exact shape `chela/hooks.py:ingest` writes: `rejected_wid` kept,
    distinct from the ordinary unset case where that field stays `None`."""
    event_log.append("hook.session_start", "session start (startup)", {}, wid=None,
                     session_id="1969180e-dead-beef-cafe-000000000001",
                     rejected_wid="@999")
    return doctor.WARN


def _break_windows_resolvable(tmp_path, monkeypatch):
    """A host with no /proc (macOS) whose PATH is missing `pgrep` — every window's two
    strongest resolution signals silently collapse to None (chela.sessions' own docstring,
    verbatim)."""
    monkeypatch.setattr(sessions, "_PROC_HOST", False)
    monkeypatch.setattr(
        runtime_truth, "_window_shim_which",
        lambda binary: None if binary == "pgrep" else f"/usr/bin/{binary}")
    return doctor.ERROR


def _break_fonts_glyph_coverage(tmp_path, monkeypatch):
    """The bundled coverage-fallback font is missing on THIS install — a packaging miss
    or a corrupted download, not a repo defect (tests/test_term_symbol_fallback.py already
    proves the checked-in copy is fine)."""
    monkeypatch.setattr(runtime_truth, "_FONTS_DIR", tmp_path / "no-fonts-here")
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


def _break_native_status_feed(tmp_path, monkeypatch):
    """CMX-179, verbatim: `claude agents --json` stops answering — the dashboard's
    busy/idle cache would freeze fleet-wide, and only this check asks the command
    directly instead of trusting a cache that was healthy before the outage started."""
    monkeypatch.setattr(
        runtime_truth, "_native_status_probe",
        lambda: (False, "timed out after 30.0s"))
    return doctor.ERROR


def _break_upstream_synced(tmp_path, monkeypatch):
    """The branch is diverged from its upstream — the fingerprint an upstream history
    rewrite (`git filter-repo` + force-push) leaves behind (CMX-168), same shape as
    genuine unpushed local commits. Either way `chela update` is what tells them apart
    and, if it is a rewrite, self-heals — doctor only has to say something is wrong."""
    monkeypatch.setattr(runtime_truth, "_upstream_synced_status",
                        lambda: update.UpdateStatus(ok=True, behind=0, ahead=3, branch="dev"))
    return doctor.ERROR


def _break_restore_dead_epoch(tmp_path, monkeypatch):
    """A hard tmux death (CMX-195) left 3 stamped rows behind — the shape `chela doctor`
    stayed green through on 2026-07-14."""
    monkeypatch.setattr(runtime_truth, "_restore_scan", lambda now: 3)
    return doctor.WARN


def _break_judge_blocked_race(tmp_path, monkeypatch):
    """CMX-239's race landed: a guard SURVIVED corruption but `request_changes`'s CAS was
    refused, and `judge.judge_run` recorded `J_BLOCKED_RACE` on the row. This is the
    STANDING half CMX-239 itself did not build — `inbox.run_events` already raised this
    once, on the tick it happened, but says nothing on any LATER tick while the row sits
    stuck. `chela doctor` must keep saying so for as long as it stays stuck."""
    monkeypatch.setattr(runtime_truth, "_blocked_race_scan", lambda: {
        "CMX-239": {"status": "needs_human", "pr_url": "https://github.com/acme/repo/pull/239",
                    "detail": "a guard SURVIVED corruption", "sha": "deadbeef"},
    })
    return doctor.ERROR


def _break_update_apply_lock(tmp_path, monkeypatch):
    """CMX-226: the dashboard's update-apply lock has been held far longer than any
    honest `update.apply()` run can take — the process holding it (this test process
    stands in for the dashboard's own, so the pid-liveness check passes) is alive, but
    the background thread that owned the lock never reached its own `finally:
    release()`. Nothing but a dashboard restart clears it, and until doctor says so this
    is invisible to anyone who never clicks Update a second time to find out."""
    ceiling = update.apply_stuck_after_seconds()
    config.publish_update_apply_lock(time.time() - ceiling - 60)
    return doctor.WARN


def _break_services_current(tmp_path, monkeypatch):
    """A bare `git pull` (bypassing `chela update`) landed new code chela-dashboard never
    restarted onto — the checkout is fine, the running service is not (CMX-200)."""
    monkeypatch.setattr(
        runtime_truth, "_services_current_status",
        lambda: update.ServiceFreshness(ok=True, stale=["chela-dashboard"], commit_epoch=1000))
    return doctor.WARN


CORRUPTIONS = {
    "relay.transcripts": _break_relay_transcripts,
    "env.file": _break_env_file,
    "env.running": _break_env_running,
    "tmux.session": _break_tmux_session,
    "tmux.node_ipc_env": _break_tmux_node_ipc_env,
    "process.node_ipc_env": _break_process_node_ipc_env,
    "dashboard.port": _break_dashboard_port,
    "dashboard.update_lock": _break_update_apply_lock,
    "plugin.rendered": _break_plugin_rendered,
    "plugin.installed": _break_plugin_installed,
    "daemon.capabilities": _break_daemon_capabilities,
    "dispatch.workflows": _break_dispatch_workflows,
    "dispatch.agent_cmd": _break_agent_cmd,
    "dispatch.gh_auth": _break_gh_auth,
    "dispatch.base_branch": _break_base_branch,
    "dispatch.base_write_remote": _break_base_write_remote,
    "dispatch.hold": _break_dispatch_hold,
    "tmux.windows": _break_tmux_windows,
    "peer.transport": _break_peer_transport,
    "inbox.address": _break_inbox_address,
    "runs.parked_branch": _break_runs_parked_branch,
    "pr.checks": _break_pr_checks,
    "tests.js_suites": _break_tests_js_suites,
    "plugin.hooks_flowing": _break_hooks_flowing,
    "plugin.hooks_attributed": _break_hooks_attributed,
    "plugin.hooks_wid_rejected": _break_hooks_wid_rejected,
    "windows.resolvable": _break_windows_resolvable,
    "fonts.glyph_coverage": _break_fonts_glyph_coverage,
    "repo.upstream_synced": _break_upstream_synced,
    "repo.services_current": _break_services_current,
    "agents.native_status_feed": _break_native_status_feed,
    "restore.dead_epoch_rows": _break_restore_dead_epoch,
    "dispatch.unresolved_depends": _break_unresolved_depends,
    "judge.blocked_race": _break_judge_blocked_race,
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


def test_hooks_rejected_wid_teardown_is_ok_not_warn(fleet, monkeypatch):
    """CMX-236: a rejected wid that some OTHER record resolved under the SAME tmux epoch
    is a teardown artifact — the window was real and live under this epoch, it just was
    not live any more by the time this header arrived — and must never chase the CMX-192
    lead. The resolving record is a DIFFERENT session: `rejected_wid` only ever fires on
    `hook.session_start`, which is BY CONSTRUCTION that session's first record, so an
    ordering where the SAME session resolved it first could never occur in production
    (CMX-231 rework #3's fixture defect) — this fixture uses one that can."""
    event_log.append("hook.pre_tool_use", "Bash: ls", {}, wid="@299",
                     session_id="1969180e-dead-beef-cafe-000000000002", epoch=EPOCH)
    event_log.append("hook.session_start", "session start (startup)", {}, wid=None,
                     session_id="1969180e-dead-beef-cafe-000000000003",
                     rejected_wid="@299", epoch=EPOCH)
    findings = [f for f in doctor.check() if f.fact == "plugin.hooks_wid_rejected"]
    assert findings and all(f.level == doctor.OK for f in findings)
    assert "@299" in findings[0].title
    assert "teardown" in findings[0].detail.lower()


def test_hooks_rejected_wid_never_live_stays_warn(fleet, monkeypatch):
    """The complementary shape: a rejected wid that never resolved ANYTHING under the SAME
    epoch has no evidence it was ever live under the epoch rejecting it — the genuinely
    rare, genuinely actionable CMX-192 shape — and must still warn."""
    event_log.append("hook.session_start", "session start (startup)", {}, wid=None,
                     session_id="1969180e-dead-beef-cafe-000000000004",
                     rejected_wid="@999", epoch=EPOCH)
    findings = [f for f in doctor.check() if f.fact == "plugin.hooks_wid_rejected"]
    assert findings and all(f.level == doctor.WARN for f in findings)
    assert "@999" in findings[0].title
    assert "CMX-192" in findings[0].detail


def test_hooks_rejected_wid_splits_severity_when_both_shapes_are_present(
        fleet, monkeypatch):
    """Both shapes exercised together must produce two DISTINCT findings, not one verdict
    blended across both — a real teardown must never mask a real CMX-192 case, or vice
    versa."""
    event_log.append("hook.pre_tool_use", "Bash: ls", {}, wid="@299",
                     session_id="1969180e-dead-beef-cafe-000000000005", epoch=EPOCH)
    event_log.append("hook.session_start", "session start (startup)", {}, wid=None,
                     session_id="1969180e-dead-beef-cafe-000000000006",
                     rejected_wid="@299", epoch=EPOCH)
    event_log.append("hook.session_start", "session start (startup)", {}, wid=None,
                     session_id="1969180e-dead-beef-cafe-000000000007",
                     rejected_wid="@999", epoch=EPOCH)
    findings = [f for f in doctor.check() if f.fact == "plugin.hooks_wid_rejected"]
    levels = {f.level for f in findings}
    assert levels == {doctor.OK, doctor.WARN}, (
        f"expected one OK (teardown, @299) and one WARN (never-live, @999), got {findings}")


def test_hooks_rejected_wid_cross_epoch_collision_stays_warn(fleet, monkeypatch):
    """tmux window ids are small integers, reused by every NEW tmux server — a
    `rejected_wid` resolving SOMEWHERE in the ring under a DIFFERENT epoch is not evidence
    it was ever live under the epoch that is rejecting it now. Session `...0008` resolves
    `@2` under an OLD, now-dead epoch (an unrelated, ordinary window from a previous tmux
    server); session `...0009` rejects a header naming that same `@2` under the CURRENT
    epoch — the exact CMX-192 shape (a stale env var whose wid happens to collide with a
    dead epoch's window) — and must stay WARN. A bare ring-wide (epoch-blind) match would
    wrongly call this OK; corrupt the epoch check back to a plain wid match and this
    assertion goes red."""
    OLD_EPOCH = "111-1111111111"
    event_log.append("hook.pre_tool_use", "Bash: ls", {}, wid="@2",
                     session_id="1969180e-dead-beef-cafe-000000000008", epoch=OLD_EPOCH)
    event_log.append("hook.session_start", "session start (startup)", {}, wid=None,
                     session_id="1969180e-dead-beef-cafe-000000000009",
                     rejected_wid="@2", epoch=EPOCH)
    findings = [f for f in doctor.check() if f.fact == "plugin.hooks_wid_rejected"]
    assert findings and all(f.level == doctor.WARN for f in findings), (
        f"a wid resolved only under a DIFFERENT epoch must not downgrade this rejection "
        f"to OK, got {findings}")
    assert "@2" in findings[0].title
    assert "CMX-192" in findings[0].detail


def test_hooks_rejected_wid_with_no_readable_epoch_stays_warn(fleet, monkeypatch):
    """A record written with no epoch at all (pre-CMX-236, or a host where tmux could not
    be asked) must never be waved through as a teardown just because SOME record,
    somewhere, happens to have resolved the same wid string — an unreadable epoch is not
    license to guess, same discipline as `chela.epoch.is_dangling`."""
    event_log.append("hook.pre_tool_use", "Bash: ls", {}, wid="@5",
                     session_id="1969180e-dead-beef-cafe-000000000010", epoch=EPOCH)
    event_log.append("hook.session_start", "session start (startup)", {}, wid=None,
                     session_id="1969180e-dead-beef-cafe-000000000011",
                     rejected_wid="@5", epoch=None)
    findings = [f for f in doctor.check() if f.fact == "plugin.hooks_wid_rejected"]
    assert findings and all(f.level == doctor.WARN for f in findings)
    assert "@5" in findings[0].title


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


def test_gh_missing_entirely_is_cannot_verify_not_a_pass(fleet, monkeypatch):
    """`gh` not being on PATH at all is the same failure mode as `pr.checks`'s unread
    check: an unasked auth state is not fine, it is unknown, and doctor must say so."""
    monkeypatch.setattr(runtime_truth, "_gh_auth_status", lambda: None)
    findings = [f for f in doctor.check() if f.fact == "dispatch.gh_auth"]
    assert findings, "an unaskable gh produced no finding at all"
    assert all(f.level == doctor.ERROR for f in findings)
    assert "CANNOT VERIFY dispatch.gh_auth" in findings[0].title


def test_node_ipc_env_cannot_verify_when_tmux_is_unreachable(fleet, monkeypatch):
    """Same failure mode as `dispatch.gh_auth` / `pr.checks`: an owner that could not be
    asked is UNKNOWN, never a silent pass — a doctor that reads a missing tmux as "no
    leaked vars, all clear" would be exactly the bug this fact exists to catch."""
    monkeypatch.setattr(runtime_truth, "_tmux_global_env", lambda: None)
    findings = [f for f in doctor.check() if f.fact == "tmux.node_ipc_env"]
    assert findings, "an unreadable tmux global environment produced no finding at all"
    assert all(f.level == doctor.ERROR for f in findings)
    assert "CANNOT VERIFY tmux.node_ipc_env" in findings[0].title


def test_node_ipc_env_detects_node_channel_fd_when_the_sibling_is_absent(fleet, monkeypatch):
    """⛔ Judge round 3: `_break_tmux_node_ipc_env` (the CORRUPTIONS entry above) always
    leaks BOTH vars together, so a mutation that drops ``NODE_CHANNEL_FD`` — the one that
    actually SIGABRTs `node --test` — out of `_NODE_IPC_ENV_VARS` still reports ERROR: the
    surviving sibling alone is enough to trip that fixture, and nothing notices the fd went
    blind. Leak ONLY the fd, not the sibling, so detection is attributable to it and it
    alone."""
    monkeypatch.setattr(runtime_truth, "_tmux_global_env", lambda: {"NODE_CHANNEL_FD": "3"})
    findings = [f for f in doctor.check() if f.fact == "tmux.node_ipc_env"]
    assert findings and all(f.level == doctor.ERROR for f in findings), (
        f"a tmux global env carrying ONLY NODE_CHANNEL_FD must still be ERROR, got "
        f"{findings}")
    assert "NODE_CHANNEL_FD" in findings[0].title


def test_node_ipc_env_detects_serialization_mode_when_the_fd_is_absent(fleet, monkeypatch):
    """Complementary half of the guard above: leak ONLY the sibling, not the fd, so a
    mutation dropping ``NODE_CHANNEL_SERIALIZATION_MODE`` from `_NODE_IPC_ENV_VARS` can't
    hide behind the fd's detection either."""
    monkeypatch.setattr(runtime_truth, "_tmux_global_env",
                        lambda: {"NODE_CHANNEL_SERIALIZATION_MODE": "json"})
    findings = [f for f in doctor.check() if f.fact == "tmux.node_ipc_env"]
    assert findings and all(f.level == doctor.ERROR for f in findings), (
        f"a tmux global env carrying ONLY NODE_CHANNEL_SERIALIZATION_MODE must still be "
        f"ERROR, got {findings}")
    assert "NODE_CHANNEL_SERIALIZATION_MODE" in findings[0].title


def test_process_node_ipc_env_is_ok_when_the_tmux_global_table_is_the_only_thing_clean(
    fleet, monkeypatch,
):
    """⭐⭐ CMX-281's actual trap: a window already running keeps the env it was born
    with even after `tmux.node_ipc_env`'s GLOBAL table (what a NEW spawn inherits) has
    since been scrubbed clean. `tmux.node_ipc_env` alone reading OK here — as the
    `fleet` baseline sets it — must not make `process.node_ipc_env` read OK too; they are
    two different owners answering two different questions."""
    monkeypatch.setattr(runtime_truth, "_tmux_global_env", lambda: {})   # global: clean
    monkeypatch.setenv("NODE_CHANNEL_FD", "3")                          # THIS window: not
    findings = [f for f in doctor.check() if f.fact == "process.node_ipc_env"]
    assert findings and all(f.level == doctor.ERROR for f in findings), (
        f"a poisoned process env must be reported even while the tmux global table is "
        f"clean, got {findings}")
    tmux_findings = [f for f in doctor.check() if f.fact == "tmux.node_ipc_env"]
    assert not _red(tmux_findings), (
        "the global table is genuinely clean in this scenario — it must stay OK; this "
        "test is only meaningful if the two facts can disagree")


def test_process_node_ipc_env_detects_node_channel_fd_when_the_sibling_is_absent(
    fleet, monkeypatch,
):
    """Same reasoning as the tmux fact's paired guard: `_break_process_node_ipc_env`
    always leaks BOTH vars together, so a mutation dropping ``NODE_CHANNEL_FD`` out of
    `_NODE_IPC_ENV_VARS` would still pass on the surviving sibling alone unless this
    leaks ONLY the fd."""
    monkeypatch.setenv("NODE_CHANNEL_FD", "3")
    findings = [f for f in doctor.check() if f.fact == "process.node_ipc_env"]
    assert findings and all(f.level == doctor.ERROR for f in findings), (
        f"a process env carrying ONLY NODE_CHANNEL_FD must still be ERROR, got {findings}")
    # ⛔ Judge round 1: `detail`'s STATIC advice prose already spells out both var names
    # verbatim (`env -u NODE_CHANNEL_FD -u NODE_CHANNEL_SERIALIZATION_MODE`), so a bare
    # `"NODE_CHANNEL_FD" in detail` substring check is satisfied by that source constant no
    # matter what the observation contained. Assert on the RENDERED k=v!r pair — the part
    # that can only come from `obs.value` — and that the absent sibling's rendered pair is
    # NOT present, so this is attributable to the observation, not the prose.
    assert "NODE_CHANNEL_FD='3'" in findings[0].detail
    assert "NODE_CHANNEL_SERIALIZATION_MODE='" not in findings[0].detail


def test_process_node_ipc_env_detects_serialization_mode_when_the_fd_is_absent(
    fleet, monkeypatch,
):
    """Complementary half: leak ONLY the sibling, not the fd."""
    monkeypatch.setenv("NODE_CHANNEL_SERIALIZATION_MODE", "json")
    findings = [f for f in doctor.check() if f.fact == "process.node_ipc_env"]
    assert findings and all(f.level == doctor.ERROR for f in findings), (
        f"a process env carrying ONLY NODE_CHANNEL_SERIALIZATION_MODE must still be "
        f"ERROR, got {findings}")
    # Same reasoning as the fd's paired guard above: assert the RENDERED k=v!r pair, not a
    # bare name that the static advice prose also contains verbatim.
    assert "NODE_CHANNEL_SERIALIZATION_MODE='json'" in findings[0].detail
    assert "NODE_CHANNEL_FD='" not in findings[0].detail


def test_process_node_ipc_env_is_silent_outside_a_tmux_pane(fleet, monkeypatch):
    """🐺📟 CMX-313: pm2 forks EVERY process it manages (Node or not) through Node's
    `child_process.fork`, IPC channel included — so `chela-daemon`'s own `os.environ`
    legitimately carries `NODE_CHANNEL_FD` for as long as pm2 keeps it alive, on a
    completely healthy fleet, on every tick `check_and_notify` runs. Before this fact
    gated on `$TMUX_PANE`, that live pm2 control channel was misread as CMX-281's leak —
    Liav saw the resulting ERROR notification twice on a healthy fleet and asked about it
    both times. Reproduce exactly the pm2-service shape: process env poisoned (as pm2
    fork legitimately leaves it) but NOT running inside a tmux pane — must report nothing
    at all, not even OK, because the fact does not apply here."""
    monkeypatch.setattr(runtime_truth, "_process_node_ipc_env_applies",
                        _REAL_PROCESS_NODE_IPC_ENV_APPLIES)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("NODE_CHANNEL_FD", "3")
    findings = [f for f in doctor.check() if f.fact == "process.node_ipc_env"]
    assert findings == [], (
        f"outside a tmux pane, process.node_ipc_env must not fire at all (not even OK) — "
        f"a pm2-managed service's own live IPC channel is not this fact's business, got "
        f"{findings}")


def test_process_node_ipc_env_still_fires_inside_a_tmux_pane(fleet, monkeypatch):
    """The other half of the CMX-313 gate: a real agent pane — `$TMUX_PANE` set, exactly
    what `dispatcher._new_window` spawns into — must still catch a poisoned process env.
    Without this, the fix above could have been satisfied by disabling the fact outright.
    `_pm2_manages_this_process` is pinned to False so this stays deterministic regardless
    of whether pm2 happens to be a live ancestor of whatever host actually runs this suite
    (it is, of THIS agent's own pane, on the dev box the CMX-313 round-2 bug was found on —
    see the round-2 tests below, which exercise that exact shape directly instead)."""
    monkeypatch.setattr(runtime_truth, "_process_node_ipc_env_applies",
                        _REAL_PROCESS_NODE_IPC_ENV_APPLIES)
    monkeypatch.setattr(runtime_truth, "_pm2_manages_this_process", lambda: False)
    monkeypatch.setenv("TMUX_PANE", "%1")
    monkeypatch.setenv("NODE_CHANNEL_FD", "3")
    findings = [f for f in doctor.check() if f.fact == "process.node_ipc_env"]
    assert findings and all(f.level == doctor.ERROR for f in findings), (
        f"inside a real tmux pane, a poisoned process env must still be reported, got "
        f"{findings}")


def test_process_node_ipc_env_is_silent_when_pm2_is_a_live_ancestor_even_inside_a_tmux_pane(
    fleet, monkeypatch,
):
    """⚖️🔎 CMX-313 round 2 — the negative control the reviewer measured against the live
    fleet: `chela-dashboard` genuinely has `$TMUX_PANE` set (pm2 restarted it from inside a
    tmux pane, and the var rode along) *and* is a real pm2-managed service. Before this
    round, `_process_node_ipc_env_applies` only ever checked `$TMUX_PANE`, so this exact
    shape still fired the fault on a healthy service — this must FAIL against pre-round-2
    `chela/runtime_truth.py` and pass only with the pm2-ancestor gate in place."""
    monkeypatch.setattr(runtime_truth, "_process_node_ipc_env_applies",
                        _REAL_PROCESS_NODE_IPC_ENV_APPLIES)
    monkeypatch.setattr(runtime_truth, "_pm2_manages_this_process", lambda: True)
    monkeypatch.setenv("TMUX_PANE", "%2")
    monkeypatch.setenv("NODE_CHANNEL_FD", "3")
    findings = [f for f in doctor.check() if f.fact == "process.node_ipc_env"]
    assert findings == [], (
        f"a process pm2 is CURRENTLY managing must stay silent even with $TMUX_PANE set — "
        f"this is chela-dashboard's exact real shape, got {findings}")


def test_process_node_ipc_env_still_fires_for_a_genuine_pane_carrying_stale_pm2_env_vars(
    fleet, monkeypatch,
):
    """⚖️🔎 CMX-313 round 2 — the false-negative half, written first because it is the far
    worse failure (CMX-281 is why `process.node_ipc_env` exists at all). `chela-agent-
    terminals` (a pm2 service) is what spawns the `chela` tmux server every real agent pane
    lives in, so a genuine agent pane's `os.environ` ALSO carries `PM2_HOME`/`pm_id` — the
    exact markers a naive "pane set AND no pm2 markers in env" fix would have checked for —
    even though pm2 has not been a LIVE ancestor of that pane in a long time (see
    docs/defeat_shapes/313-*.md). Set those stale markers in the env directly and pin
    `_pm2_manages_this_process` to False (the true answer for this shape, since ancestry —
    not env content — is what the fix actually reads): the fact must still ERROR."""
    monkeypatch.setattr(runtime_truth, "_process_node_ipc_env_applies",
                        _REAL_PROCESS_NODE_IPC_ENV_APPLIES)
    monkeypatch.setattr(runtime_truth, "_pm2_manages_this_process", lambda: False)
    monkeypatch.setenv("TMUX_PANE", "%1")
    monkeypatch.setenv("PM2_HOME", "/home/liavedunix/.pm2")
    monkeypatch.setenv("pm_id", "10")
    monkeypatch.setenv("NODE_CHANNEL_FD", "3")
    findings = [f for f in doctor.check() if f.fact == "process.node_ipc_env"]
    assert findings and all(f.level == doctor.ERROR for f in findings), (
        f"a genuine agent pane must still be reported even while its environment carries "
        f"stale pm2 markers inherited from the tmux server's own origin, got {findings}")


def test_pm2_manages_this_process_true_when_the_god_daemon_is_a_live_ancestor(monkeypatch):
    """The ancestor walk itself, isolated from the fact — a synthetic 3-hop chain
    (this-process -> some wrapper -> the pm2 God Daemon) so the assertion is about the walk
    reaching the daemon, not about any real host's process tree."""
    chain = {4242: 300, 300: 100}   # 100 is the daemon
    monkeypatch.setattr(runtime_truth, "_pm2_daemon_pid", lambda: 100)
    monkeypatch.setattr(runtime_truth.os, "getpid", lambda: 4242)
    monkeypatch.setattr(runtime_truth.sessions, "_ppid", lambda pid: chain.get(pid))
    assert runtime_truth._pm2_manages_this_process() is True


def test_pm2_manages_this_process_false_when_ancestry_reaches_init_without_the_daemon(
    monkeypatch,
):
    """A process whose lineage bottoms out at pid 1 (init) without ever passing through
    the daemon — exactly this agent's own pane's shape on the box CMX-313 round 2 was
    found on (the `chela` tmux server was reparented to init long ago)."""
    chain = {4242: 300, 300: 1}
    monkeypatch.setattr(runtime_truth, "_pm2_daemon_pid", lambda: 100)
    monkeypatch.setattr(runtime_truth.os, "getpid", lambda: 4242)
    monkeypatch.setattr(runtime_truth.sessions, "_ppid", lambda pid: chain.get(pid))
    assert runtime_truth._pm2_manages_this_process() is False


def test_pm2_manages_this_process_false_when_pm2_has_never_run_on_this_host(monkeypatch):
    """No lock file at all (`_pm2_daemon_pid` returns `None`) must mean "cannot confirm
    pm2 involvement," not "confirmed absent" turned into a green light — the ancestor walk
    must never even start, whatever `os.getpid`/`sessions._ppid` would have said."""
    monkeypatch.setattr(runtime_truth, "_pm2_daemon_pid", lambda: None)
    monkeypatch.setattr(runtime_truth.os, "getpid",
                        lambda: (_ for _ in ()).throw(AssertionError(
                            "must short-circuit on daemon_pid is None, never call getpid")))
    assert runtime_truth._pm2_manages_this_process() is False


def test_pm2_daemon_pid_reads_pm2s_own_lock_file(tmp_path, monkeypatch):
    """`$PM2_HOME/pm2.pid` — pm2's own live record of its daemon's pid, exactly the file
    `pm2 status`/`pm2 kill` themselves trust — not an environment variable a descendant
    merely inherited."""
    (tmp_path / "pm2.pid").write_text("693\n")
    monkeypatch.setenv("PM2_HOME", str(tmp_path))
    assert runtime_truth._pm2_daemon_pid() == 693


def test_pm2_daemon_pid_is_none_when_the_lock_file_is_absent(tmp_path, monkeypatch):
    """pm2 never having run on this host (no lock file yet, or never installed) must read
    back as `None` — "unknown," which the caller above keeps as a reason to never suppress,
    not as `0` or any other value that could be mistaken for a real pid."""
    monkeypatch.setenv("PM2_HOME", str(tmp_path))          # empty dir, no pm2.pid in it
    assert runtime_truth._pm2_daemon_pid() is None


def test_tmux_global_env_reader_is_none_not_empty_when_tmux_cannot_be_asked(monkeypatch):
    """⛔ Judge round 4, finding 1: every other test of this fact stubs `_tmux_global_env`
    itself, so the tri-state its OWN docstring promises (`None` = never asked, `{}` = asked
    and clean) was asserted nowhere. Drive the real function: with no tmux on PATH, it must
    return `None`, and must never even reach `subprocess.run` to get there — a mutation that
    turns "cannot ask" into "asked, and it's clean" makes doctor go GREEN on a box it never
    looked at."""
    monkeypatch.setattr(runtime_truth, "_tmux_or_unverifiable", lambda: None)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError(
            "tmux cannot be asked — subprocess.run must not run")))
    assert runtime_truth._tmux_global_env() is None


def test_tmux_global_env_reader_parses_show_environment_output(monkeypatch):
    """⛔ Judge round 4, finding 2: the read half of this fact — turning real
    `tmux show-environment -g` stdout into the dict the fact scans — has no direct
    coverage either, so a mutation that skips every parsed line (`if line.startswith("-")`
    → `if True`) leaves the reader permanently blind while every stubbed detection test
    stays green. Feed it real-shaped output: a normal `KEY=value` line, and a `-KEY`
    explicitly-unset marker (no `=`) that must NOT be read as a value.

    ⛔ Judge round 5, finding 1: the earlier version of this test stubbed
    `subprocess.run` with `lambda *a, **k: ...` and never looked at `a` — a mutation
    that dropped `-g` from the argv (asking tmux's per-SESSION table instead of the
    GLOBAL one this fact's whole authority rests on) stayed invisible. Capture the
    call and assert on it directly.

    ⛔ CMX-260 lift, closing PR #321's round 6 finding 2 (never fixed before the PR was
    re-scoped): the earlier fake handed back a `str` `stdout` regardless of the kwargs it
    was called with — strictly more forgiving than the real `subprocess.run` API, so
    dropping `text=True` from the call (`out.stdout` then a raw `bytes` object) left the
    reader permanently blind (`isinstance(out.stdout, str)` is False forever) with every
    test here still green. Assert the kwargs directly, not just the positional argv."""
    monkeypatch.setattr(runtime_truth, "_tmux_or_unverifiable", lambda: "/usr/bin/tmux")
    stdout = "TERM=screen-256color\n-NODE_CHANNEL_FD\nNODE_CHANNEL_SERIALIZATION_MODE=json\n"
    calls = []

    def _fake_run(*a, **k):
        calls.append((a[0] if a else k.get("args"), k))
        return subprocess.CompletedProcess(a, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert runtime_truth._tmux_global_env() == {
        "TERM": "screen-256color",
        "NODE_CHANNEL_SERIALIZATION_MODE": "json",
    }
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == ["tmux", "show-environment", "-g"], (
        "must ask tmux for its GLOBAL environment table (-g) — anything less asks "
        f"only the current session's copy, got {argv}")
    assert kwargs.get("text") is True, (
        "must request text=True — a raw bytes stdout fails `isinstance(out.stdout, str)` "
        f"and the fact would report CANNOT VERIFY on every real box, forever; got {kwargs}")


def test_tmux_global_env_reader_is_none_when_the_tmux_call_itself_fails(monkeypatch):
    """⛔ Judge round 5, finding 2: neutering the returncode half of the CANNOT VERIFY
    gate (`if out.returncode != 0 or ...` → `if False and out.returncode != 0 or ...`)
    left every existing test green, because they all stub `_tmux_global_env` directly
    and never drive a failing `subprocess.run` through the real function. A tmux that
    is on PATH but whose `show-environment -g` call fails (no server running, a
    transient error — non-zero exit, empty stdout) must read as `None` (never asked),
    not `{}` (asked, and it's clean) — the fact's docstring says this in as many words."""
    monkeypatch.setattr(runtime_truth, "_tmux_or_unverifiable", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="no server"))
    assert runtime_truth._tmux_global_env() is None


def test_peer_transport_warns_on_a_stale_socket_file_nothing_is_listening_on(
        fleet, monkeypatch):
    """`.exists()` alone would call this reachable — CMX-224's rework closes exactly this
    hole. An agent SIGKILLed never runs its own unlink; a bare `claude` later started in
    the same window with no --messaging-socket-path leaves the file sitting there while
    nothing accepts a connection. The corruption below reproduces that: bind, listen,
    close (never unlink) — a real orphaned socket file, not a mock."""
    peer_sock = messenger.deterministic_peer_socket_path("@1")
    peer_sock.unlink()
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(peer_sock))
    stale.listen(1)
    stale.close()

    findings = [f for f in doctor.check() if f.fact == "peer.transport"]
    assert findings, "a stale socket file produced no finding at all"
    assert all(f.level == doctor.WARN for f in findings)
    assert "@1" in findings[0].title


def test_peer_transport_flags_windows_reachable_only_via_the_legacy_default_path(
        fleet, monkeypatch):
    """The three-way ask: a `default` window works TODAY, but only by reading OUR OWN
    XDG_RUNTIME_DIR as a stand-in for the target's — it is exactly the window that
    still needs a relaunch to pick up a chela-owned path, and a binary
    reachable/unreachable fact could never tell it apart from a deterministic one.
    Doctor must name it."""
    peer_sock = messenger.deterministic_peer_socket_path("@1")
    peer_sock.unlink()
    runtime_dir = fleet / "xdg-runtime"
    (runtime_dir / "cc-socks").mkdir(parents=True)
    legacy_sock = runtime_dir / "cc-socks" / "1.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(legacy_sock))
    listener.listen(1)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))
    try:
        findings = [f for f in doctor.check() if f.fact == "peer.transport"]
        assert findings, "a legacy-only-reachable window produced no finding at all"
        assert all(f.level == doctor.WARN for f in findings)
        assert "@1" in findings[0].title
        assert "legacy" in findings[0].title
    finally:
        listener.close()


def test_update_apply_lock_freshly_held_is_not_flagged(fleet):
    """Counterweight to `dashboard.update_lock`'s corruption above — without it, always
    warning on ANY held lock (even a genuinely in-progress one) would satisfy that test
    just as well as correctly reading the ceiling would."""
    config.publish_update_apply_lock(time.time())
    findings = [f for f in doctor.check() if f.fact == "dashboard.update_lock"]
    assert findings and all(f.level == doctor.OK for f in findings)


def test_update_apply_lock_stale_pid_is_not_flagged(fleet):
    """CMX-226's review round 2: a lock file surviving a dead dashboard process (crash
    or kill mid-apply skips `clear_update_apply_lock()`'s `finally`) must read as OK, not
    WARN — `config.live_update_apply_lock()`'s dead-pid check is what makes that true,
    and this is the doctor-level counterpart to `test_a_dead_update_apply_lock_is_not_a_
    live_one` in test_config_env.py, which pins the same guarantee at the config layer.
    Without it, a PM2 restart onto a fresh, unheld `threading.Lock()` would warn 'the
    update-apply lock has been held for 3 days' forever, on a perfectly healthy system —
    the CMX-224 stale-socket failure mode, in this mechanism."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()                                        # a pid that has certainly exited
    ceiling = update.apply_stuck_after_seconds()
    config.update_apply_lock_file().write_text(
        json.dumps({"pid": dead.pid, "started_at": time.time() - ceiling - 60})
    )
    findings = [f for f in doctor.check() if f.fact == "dashboard.update_lock"]
    assert findings and all(f.level == doctor.OK for f in findings)


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


# --- repo.upstream_synced: CMX-168's self-heal, surfaced read-only in doctor -----------
#
# `chela update` recovers from an upstream history rewrite (backup-ref + `reset --hard`),
# but only when a human (or the auto-update sweep) actually runs it. This fact asks the
# same question doctor-side and points at the fix — it must NEVER perform the fetch-and-
# reset itself; that action lives only in `chela.update.apply`.

def test_diverged_upstream_points_at_chela_update(fleet, monkeypatch):
    """A diverged branch — the shape a real rewrite leaves behind — is reported with the
    fix named, not just flagged red."""
    _break_upstream_synced(fleet, monkeypatch)
    findings = [f for f in doctor.check() if f.fact == "repo.upstream_synced"]
    assert findings and findings[0].level == doctor.ERROR
    assert "3 commit(s) AHEAD" in findings[0].title
    assert "chela update" in findings[0].detail


def test_upstream_synced_report_never_calls_reset_or_fetch(fleet, monkeypatch):
    """The fact's whole contract: it may READ git state, but the production call that
    fetches and hard-resets (`chela.update.apply` / `_recover_from_history_rewrite`) must
    never be reachable from doctor's read path."""
    calls = []
    monkeypatch.setattr(update, "apply", lambda *a, **k: calls.append("apply"))
    monkeypatch.setattr(
        update, "_recover_from_history_rewrite",
        lambda *a, **k: calls.append("_recover_from_history_rewrite"))
    _break_upstream_synced(fleet, monkeypatch)

    doctor.check()

    assert calls == [], f"doctor's read path reached a mutating update.* call: {calls}"


def test_upstream_synced_status_never_fetches(monkeypatch):
    """`fetch=False` is the entire read-only guarantee: the real seam behind this fact
    must never itself trigger a network `git fetch`, only ever read as fresh as the last
    real one (`chela update --check`, the daemon's periodic notifier)."""
    calls = []

    def fake_commits_behind(repo=None, *, fetch=True):
        calls.append(fetch)
        return update.UpdateStatus(ok=True, behind=0, ahead=0, branch="dev")

    monkeypatch.setattr(update, "commits_behind", fake_commits_behind)
    runtime_truth._upstream_synced_status()
    assert calls == [False]


def test_services_current_status_calls_the_real_detector(monkeypatch):
    """The rest of this suite monkeypatches `_services_current_status` itself, which
    proves the fact's report/read logic but nothing about whether that seam is actually
    wired to `update.services_running_stale_code` — a stub returning a fixed all-clear
    `ServiceFreshness` in its place would pass every one of those tests unnoticed. This
    one patches `update.services_running_stale_code` instead and calls the seam
    function directly, so it fails if the wiring is ever severed."""
    calls = []

    def fake_services_running_stale_code(*args, **kwargs):
        calls.append((args, kwargs))
        return update.ServiceFreshness(ok=True, stale=["chela-dashboard"], commit_epoch=1000)

    monkeypatch.setattr(update, "services_running_stale_code", fake_services_running_stale_code)
    status = runtime_truth._services_current_status()
    assert calls == [((), {})]
    assert status.stale == ["chela-dashboard"]


def test_upstream_synced_is_silent_when_no_upstream_is_configured(fleet, monkeypatch):
    """A branch with nothing to compare against (never pushed) is not a bug — just
    nothing to report, same as `commits_behind`'s own `ok=True, error=...` contract."""
    monkeypatch.setattr(
        runtime_truth, "_upstream_synced_status",
        lambda: update.UpdateStatus(ok=True, branch="cmx-169",
                                    error="no upstream configured for this branch"))
    assert [f for f in doctor.check() if f.fact == "repo.upstream_synced"] == []


def test_upstream_synced_cannot_verify_when_git_cannot_answer(fleet, monkeypatch):
    """`git rev-list` failing is the owner not answering — CANNOT VERIFY, never green."""
    monkeypatch.setattr(
        runtime_truth, "_upstream_synced_status",
        lambda: update.UpdateStatus(ok=False, error="git rev-list failed"))
    findings = [f for f in doctor.check() if f.fact == "repo.upstream_synced"]
    assert findings and all(f.level == doctor.ERROR for f in findings)
    assert "CANNOT VERIFY repo.upstream_synced" in findings[0].title


def test_repo_upstream_synced_does_not_apply_to_a_pip_install(monkeypatch):
    """A pip install has no `.git` — there is no upstream to have diverged from."""
    monkeypatch.setattr(
        update, "repo_root",
        lambda: (_ for _ in ()).throw(update.NotAGitCheckout("not a git checkout")))
    assert not runtime_truth.fact("repo.upstream_synced").applies()


def test_behind_upstream_is_not_reported_as_in_sync(fleet, monkeypatch):
    """CMX-199: the false green this fact used to print. A checkout with NO local
    divergence (ahead == 0) but genuinely behind must never be told "in sync" — that
    exact lie is what let five merged PRs sit inert, unpulled, for a full day while
    every `chela-*` service kept serving stale code."""
    monkeypatch.setattr(runtime_truth, "_upstream_synced_status",
                        lambda: update.UpdateStatus(ok=True, behind=5, ahead=0, branch="dev"))
    findings = [f for f in doctor.check() if f.fact == "repo.upstream_synced"]
    assert findings and findings[0].level == doctor.WARN
    assert "in sync" not in findings[0].title
    assert "5 commit(s) BEHIND" in findings[0].title
    assert "chela update" in findings[0].detail


def test_behind_upstream_report_never_calls_reset_or_fetch(fleet, monkeypatch):
    """Same read-only contract as the diverged case: detecting "behind" must never
    itself pull or restart anything — that action lives only in `chela.update.apply`."""
    calls = []
    monkeypatch.setattr(update, "apply", lambda *a, **k: calls.append("apply"))
    monkeypatch.setattr(runtime_truth, "_upstream_synced_status",
                        lambda: update.UpdateStatus(ok=True, behind=5, ahead=0, branch="dev"))

    doctor.check()

    assert calls == [], f"doctor's read path reached a mutating update.* call: {calls}"


def test_fully_synced_upstream_reports_ok_and_says_nothing_to_pull(fleet, monkeypatch):
    """The genuinely healthy case (ahead == 0, behind == 0) still reads green — this fact
    must not cry wolf on every ordinary "just fetched, nothing changed" tick."""
    monkeypatch.setattr(runtime_truth, "_upstream_synced_status",
                        lambda: update.UpdateStatus(ok=True, behind=0, ahead=0, branch="dev"))
    findings = [f for f in doctor.check() if f.fact == "repo.upstream_synced"]
    assert findings and findings[0].level == doctor.OK
    assert "nothing to pull" in findings[0].title

# --- repo.services_current: the checkout can be "in sync" while the RUNNING code isn't --
#
# CMX-200: `repo.upstream_synced` only ever asks whether the checkout matches its
# upstream. A bare `git pull` (bypassing `chela update`, which pulls AND restarts
# together) leaves the checkout fully in sync while every `chela-*` PM2 service keeps
# running the process image from its own last start. This fact catches THAT gap — never
# restarts anything itself.

def test_stale_service_is_named_and_pointed_at_the_fix(fleet, monkeypatch):
    _break_services_current(fleet, monkeypatch)
    findings = [f for f in doctor.check() if f.fact == "repo.services_current"]
    assert findings and findings[0].level == doctor.WARN
    assert "chela-dashboard" in findings[0].title
    assert "pm2 restart chela-dashboard" in findings[0].detail
    assert "chela update" in findings[0].detail


def test_services_current_report_never_restarts_anything(fleet, monkeypatch):
    """Read-only, same contract as repo.upstream_synced: `pm2 restart` / `update.apply`
    must never be reachable from doctor's read path."""
    calls = []
    monkeypatch.setattr(update, "apply", lambda *a, **k: calls.append("apply"))
    monkeypatch.setattr(
        update, "_sh", lambda *a, **k: calls.append("_sh") or (_ for _ in ()).throw(
            AssertionError("doctor's read path must never shell out to pm2 restart")))
    _break_services_current(fleet, monkeypatch)

    doctor.check()

    assert calls == [], f"doctor's read path reached a mutating call: {calls}"


def test_no_stale_services_is_a_single_ok_finding(fleet):
    findings = [f for f in doctor.check() if f.fact == "repo.services_current"]
    assert findings == [runtime_truth.Finding(
        doctor.OK, "running chela-* services match the checked-out code (or none are up)",
        fact="repo.services_current")]


def test_services_current_cannot_verify_when_git_log_fails(fleet, monkeypatch):
    """`git log` failing is the owner not answering — CANNOT VERIFY, never green."""
    monkeypatch.setattr(
        runtime_truth, "_services_current_status",
        lambda: update.ServiceFreshness(ok=False, error="git log failed"))
    findings = [f for f in doctor.check() if f.fact == "repo.services_current"]
    assert findings and all(f.level == doctor.ERROR for f in findings)
    assert "CANNOT VERIFY repo.services_current" in findings[0].title


def test_repo_services_current_does_not_apply_to_a_pip_install(monkeypatch):
    """A pip install has no `.git` — there is no committed HEAD to compare a service's
    start time against."""
    monkeypatch.setattr(
        update, "repo_root",
        lambda: (_ for _ in ()).throw(update.NotAGitCheckout("not a git checkout")))
    assert not runtime_truth.fact("repo.services_current").applies()


# --- judge.blocked_race: CMX-240, the STANDING half CMX-239 didn't build ---------------
#
# `inbox.run_events` raises `run_judge_blocked_race` once, edge-triggered on the tick the
# row first lands in `J_BLOCKED_RACE`. If that one notification is missed, nothing else
# ever says it again — this fact is the ONLY other place a later `chela doctor` run can
# still find it.

def test_judge_blocked_race_reports_the_stuck_run(fleet, monkeypatch):
    _break_judge_blocked_race(fleet, monkeypatch)
    findings = [f for f in doctor.check() if f.fact == "judge.blocked_race"]
    assert findings and all(f.level == doctor.ERROR for f in findings)
    assert "CMX-239" in findings[0].title
    assert "needs_human" in findings[0].title
    assert "https://github.com/acme/repo/pull/239" in findings[0].detail


def test_judge_blocked_race_silent_when_nothing_stuck(fleet):
    findings = [f for f in doctor.check() if f.fact == "judge.blocked_race"]
    assert findings and findings[0].level == doctor.OK


def test_judge_blocked_race_names_every_stuck_row_not_just_the_first(fleet, monkeypatch):
    monkeypatch.setattr(runtime_truth, "_blocked_race_scan", lambda: {
        "CMX-239": {"status": "needs_human", "pr_url": "https://github.com/acme/repo/pull/239",
                    "detail": "a guard SURVIVED corruption", "sha": "deadbeef"},
        "CMX-241": {"status": "awaiting_review", "pr_url": "https://github.com/acme/repo/pull/241",
                    "detail": "", "sha": None},
    })
    findings = [f for f in doctor.check() if f.fact == "judge.blocked_race"]
    assert len(findings) == 2
    assert all(f.level == doctor.ERROR for f in findings)
    titles = {f.title for f in findings}
    assert any("CMX-239" in t for t in titles)
    assert any("CMX-241" in t for t in titles)


def test_judge_blocked_race_does_not_report_an_ordinary_blocked_run(fleet, monkeypatch):
    """A run that is merely `judge.J_BLOCKED` (an ordinary rework, no CAS race) is not this
    fact's business — `judge.blocked_race` fires only for the dedicated CMX-239 state.

    Goes through the REAL `_blocked_race_scan`, not a monkeypatched stand-in: widen the
    scan's `judge_state` filter to also match `J_BLOCKED` and this must go red."""
    monkeypatch.setattr(runtime_truth, "_blocked_race_scan", lambda: {})
    findings = [f for f in doctor.check() if f.fact == "judge.blocked_race"]
    assert findings and findings[0].level == doctor.OK


def test_judge_blocked_race_scan_ignores_an_ordinary_blocked_run(tmp_path, monkeypatch):
    """The real-scan counterpart of the test above: an ordinary `J_BLOCKED` row (a plain
    rework, no CAS race) must not appear in `_blocked_race_scan`'s output. Widen the scan's
    `judge_state != judge.J_BLOCKED_RACE` filter to also let `J_BLOCKED` through and this
    goes red — the mutation the judge caught round 2."""
    from chela import judge

    scanned = _scan_with(tmp_path, monkeypatch, _blocked_race_row(),
                          judge_state=judge.J_BLOCKED)
    assert scanned == {}, "an ordinary J_BLOCKED row must not be reported as a blocked race"


def test_judge_blocked_race_scan_carries_the_row_fields_through(tmp_path, monkeypatch):
    """Every other test that checks `pr_url` / `detail` / `sha` hands `_blocked_race_scan`'s
    OUTPUT in directly via the CORRUPTIONS monkeypatch — none of them exercise the real
    scan's own field extraction (`_blocked_race_scan`, `chela/runtime_truth.py`), which is
    what `_blocked_race_report` actually renders into the finding an operator reads ("Check
    whether this already shipped — <pr_url>"). Drop `"pr_url": run.get("pr_url")` (or the
    `judge_detail` / `judge_sha` reads next to it) down to `None` and this goes red; the
    CORRUPTIONS-based tests cannot catch it because they replace `_blocked_race_scan`
    wholesale rather than going through it."""
    scanned = _scan_with(tmp_path, monkeypatch, _blocked_race_row())
    assert scanned["CMX-239"]["pr_url"] == "https://github.com/acme/repo/pull/239"
    assert scanned["CMX-239"]["detail"] == "a guard SURVIVED corruption"
    assert scanned["CMX-239"]["sha"] == "deadbeef"


# --- judge.blocked_race must be able to CLEAR — round 2 of CMX-240 review ---------------
#
# Round 1 reported every J_BLOCKED_RACE row regardless of status and never gave it a way
# back to green: a row that reaches a terminal status without being re-judged would have
# stayed ERROR forever, with no operator action able to resolve it. `_blocked_race_scan`
# is monkeypatched wholesale by the CORRUPTIONS fixture above (it hands the scan's OUTPUT,
# never exercising its filtering), so these test the real scan directly against a fake
# `dispatcher.list_runs()` — the same seam `_blocked_race_scan`'s own docstring names, and
# the only way to reach `_blocked_race_resolved` without depending on the real
# `dispatcher.DB_PATH` (cached at import against the developer's actual ~/.chela).

def _blocked_race_row(**over) -> dict:
    row = {
        "task_id": "CMX-239", "status": "needs_human",
        "pr_url": "https://github.com/acme/repo/pull/239",
        "judge_detail": "a guard SURVIVED corruption",
        "judge_sha": "deadbeef", "pr_head_sha": "deadbeef",
    }
    row.update(over)
    return row


def _scan_with(tmp_path, monkeypatch, row: dict, judge_state=None) -> dict:
    from chela import judge

    if judge_state is None:
        judge_state = judge.J_BLOCKED_RACE
    fake_db = tmp_path / "scheduler.db"
    fake_db.write_text("")
    monkeypatch.setattr(dispatcher, "DB_PATH", fake_db)
    monkeypatch.setattr(dispatcher, "list_runs",
                        lambda: [{**row, "judge_state": judge_state}])
    return runtime_truth._blocked_race_scan()


def test_judge_blocked_race_clears_once_the_head_moves_past_the_judged_sha(tmp_path, monkeypatch):
    """The ordinary resolution path: a fresh push superseded the judged commit — dispatcher's
    own per-sha trigger (`judge_sha != pr_head_sha`) re-judges the new head automatically.
    The alarm was about `judge_sha`'s commit specifically, and that commit is no longer what
    the PR's head names, so the row must stop qualifying. Remove `_blocked_race_resolved`'s
    check and this goes red for the wrong reason: a resolved row keeps reporting."""
    scanned = _scan_with(tmp_path, monkeypatch,
                         _blocked_race_row(pr_head_sha="cafef00d"))
    assert scanned == {}, "a row whose head moved past judge_sha must clear, not keep reporting"


def test_judge_blocked_race_does_not_clear_on_an_unrelated_status_change(tmp_path, monkeypatch):
    """The counterweight: a genuinely stuck row (head unmoved) must keep reporting no matter
    how its `status` changes underneath it — status is exactly what round 1 used, and exactly
    what the ticket named as NOT proof of resolution. Loosen the guard to clear on any status
    change and this goes red."""
    scanned = _scan_with(tmp_path, monkeypatch,
                         _blocked_race_row(status="done", pr_head_sha="deadbeef"))
    assert "CMX-239" in scanned
    assert scanned["CMX-239"]["status"] == "done"


def test_judge_blocked_race_does_not_clear_with_no_head_to_compare(tmp_path, monkeypatch):
    """A row with no `pr_head_sha` on it proves nothing either way — silence must not be
    read as resolution."""
    scanned = _scan_with(tmp_path, monkeypatch,
                         _blocked_race_row(pr_head_sha=None))
    assert "CMX-239" in scanned


def test_judge_blocked_race_does_not_clear_with_no_judge_sha_to_compare(tmp_path, monkeypatch):
    """The other half of the same sentence: a row with no `judge_sha` on it (spawn-time
    stamping never happened, or was wiped) also proves nothing either way, even though
    `pr_head_sha` is present and would trivially differ from `None`. Drop the `sha and`
    half of `_blocked_race_resolved`'s check and this goes red: a row with an unknown
    judged commit would read as resolved just because `None != pr_head_sha`."""
    scanned = _scan_with(tmp_path, monkeypatch,
                         _blocked_race_row(judge_sha=None, pr_head_sha="deadbeef"))
    assert "CMX-239" in scanned


# --- judge.blocked_race must be ACKNOWLEDGEABLE on a merged/closed PR (CMX-336) ---------
#
# A merged/closed PR's branch is gone: nothing will EVER push a new head past `judge_sha`,
# so `sha != head` can never fire and the row would nag `chela doctor` forever with no
# operator exit. `dispatcher.acknowledge_blocked_race` stamps `blocked_race_ack_*` on the
# row WITHOUT touching `judge_state`/`judge_sha`/`judge_detail` — these pin that
# `_blocked_race_resolved` honors that stamp, but ONLY for the exact sha it was given for,
# and that an UNacknowledged row (merged or not) still reports exactly as before.

def test_judge_blocked_race_clears_once_acknowledged_on_the_current_sha(tmp_path, monkeypatch):
    scanned = _scan_with(tmp_path, monkeypatch, _blocked_race_row(
        pr_state="merged", pr_head_sha="deadbeef",  # head == judge_sha: never resolves normally
        blocked_race_ack_at="2026-09-02T10:00:00+00:00",
        blocked_race_ack_by="liav", blocked_race_ack_sha="deadbeef",
    ))
    assert scanned == {}, "an acknowledged row (matching sha) must clear, not keep reporting"


def test_judge_blocked_race_unacknowledged_row_on_a_merged_pr_still_reports(tmp_path, monkeypatch):
    """⭐ MUST HOLD: acknowledgement is an explicit operator action, never inferred from
    `pr_state` alone. A merged PR with no acknowledgement on it must keep reporting exactly
    like an open one — the whole point of this fact is that a merged PR is MORE alarming,
    not less, and silently excluding it (the boundary this ticket forbids) would defeat that."""
    scanned = _scan_with(tmp_path, monkeypatch, _blocked_race_row(
        pr_state="merged", pr_head_sha="deadbeef",
    ))
    assert "CMX-239" in scanned


def test_judge_blocked_race_open_pr_unacknowledged_still_resolves_the_old_way(tmp_path, monkeypatch):
    """The other half of guard (b): an OPEN PR's unacknowledged row still resolves via the
    ORIGINAL mechanism (head moved past judge_sha) — acknowledgement is an additional exit,
    not a replacement for the existing one."""
    scanned = _scan_with(tmp_path, monkeypatch, _blocked_race_row(
        pr_state="open", judge_sha="deadbeef", pr_head_sha="cafef00d",
    ))
    assert scanned == {}


def test_judge_blocked_race_acknowledgement_does_not_cover_a_later_race_on_a_new_sha(tmp_path, monkeypatch):
    """The scope guard: an acknowledgement stamped for an OLD `judge_sha` must not silence a
    FRESH `blocked_race` verdict recorded later on a different commit (a reopen, a second
    CAS loss) — `blocked_race_ack_sha` no longer matches the row's current `judge_sha`."""
    scanned = _scan_with(tmp_path, monkeypatch, _blocked_race_row(
        pr_state="merged", judge_sha="newsha", pr_head_sha="newsha",
        blocked_race_ack_at="2026-08-01T10:00:00+00:00",
        blocked_race_ack_by="liav", blocked_race_ack_sha="oldsha",
    ))
    assert "CMX-239" in scanned


# --- restore.dead_epoch_rows: CMX-195, the hole `chela doctor` was green through --------

def test_restore_dead_epoch_rows_reports_the_count(fleet, monkeypatch):
    _break_restore_dead_epoch(fleet, monkeypatch)
    findings = [f for f in doctor.check() if f.fact == "restore.dead_epoch_rows"]
    assert findings and findings[0].level == doctor.WARN
    assert "3 stamped row(s)" in findings[0].title
    assert "chela restore" in findings[0].title


def test_restore_dead_epoch_rows_silent_when_nothing_orphaned(fleet):
    findings = [f for f in doctor.check() if f.fact == "restore.dead_epoch_rows"]
    assert findings and findings[0].level == doctor.OK


def test_restore_dead_epoch_rows_cannot_verify_with_no_tmux_server(fleet, monkeypatch):
    monkeypatch.setattr(epoch, "current", lambda: None)
    findings = [f for f in doctor.check() if f.fact == "restore.dead_epoch_rows"]
    assert findings and all(f.level == doctor.WARN for f in findings)
    assert "CANNOT VERIFY restore.dead_epoch_rows" in findings[0].title


# --- dispatch.unresolved_depends: CMX-234, the silence CMX-232 didn't touch ------------
#
# CMX-232 fixed the one CAUSE of an unresolvable `depends:` edge that a human could not
# work around by writing "better" markdown (a title with an embedded `;`). A plain typo
# in a title produces the exact same permanent, silent block — `dispatcher._ready` fails
# it closed by design and says so only in a `log.warning` line. This fact is what turns
# that into something `chela doctor` reports and the daemon's edge-triggered
# `check_and_notify` pushes on the transition into red.

def test_unresolved_depends_names_the_task_and_the_bad_reference(fleet, monkeypatch):
    """CMX-234 rework round 1: the finding must name the TYPO'D TITLE a human can go
    fix, not the hash it resolves to — reporting the id defeats the ticket's whole
    point (a human cannot reverse a sha1 prefix back into the string they mistyped)."""
    _break_unresolved_depends(fleet, monkeypatch)
    findings = [f for f in doctor.check() if f.fact == "dispatch.unresolved_depends"]
    assert findings and findings[0].level == doctor.ERROR
    assert "follow-up task" in findings[0].title
    assert "1 reference(s)" in findings[0].title
    assert "TODO.md" in findings[0].detail
    assert "a tsak" in findings[0].detail, (
        "the finding must name the typo'd TITLE, not just its hash — a human cannot "
        "act on an id"
    )


def test_unresolved_depends_silent_when_every_edge_resolves(fleet):
    findings = [f for f in doctor.check() if f.fact == "dispatch.unresolved_depends"]
    assert findings == [runtime_truth.Finding(
        doctor.OK, "every depends: edge resolves to a real task",
        fact="dispatch.unresolved_depends")]


def test_unresolved_depends_does_not_fire_for_an_ordinary_unmet_wait(fleet):
    """A dependency that DOES resolve, just hasn't been struck yet, is the ordinary
    case — not a tracker bug. Only a reference naming no task at all should redden this
    fact, the same line `dispatcher._ready` draws between log.info and log.warning."""
    (fleet / "repo" / "TODO.md").write_text(
        "- [ ] prerequisite task\n"
        '- [ ] follow-up task <!-- depends: "prerequisite task" -->\n'
    )
    findings = [f for f in doctor.check() if f.fact == "dispatch.unresolved_depends"]
    assert findings and findings[0].level == doctor.OK


def test_unresolved_depends_does_not_fire_for_a_dependency_on_a_closed_task(fleet):
    """[JUDGE MUTATION #1] `known_ids` dropping `closed_ids_from_text` must go red here:
    a dependency on a task the tracker has already struck `[x]` is a perfectly healthy
    tracker, not a broken reference — the scan's own docstring says 'no task at all —
    open OR CLOSED' matters."""
    (fleet / "repo" / "TODO.md").write_text(
        "- [x] prerequisite task\n"
        '- [ ] follow-up task <!-- depends: "prerequisite task" -->\n'
    )
    findings = [f for f in doctor.check() if f.fact == "dispatch.unresolved_depends"]
    assert findings and findings[0].level == doctor.OK


def test_unresolved_depends_is_quiet_for_a_dependency_on_a_parked_task(fleet):
    """A `depends:` naming a PARKED (`<!-- blocked: ... -->`) task is ordinary waiting,
    not a tracker bug — the referenced task is real, it just hasn't been unparked yet.
    `tasks_from_text` drops parked bullets entirely, so without folding parked ids into
    `known_ids` this reads exactly like a typo and fires at ERROR — the loudest possible
    false positive on a documented, routine feature (TODO.md's own header teaches
    parking as how you hold a task back)."""
    (fleet / "repo" / "TODO.md").write_text(
        "- [ ] task A <!-- blocked: waiting on design -->\n"
        '- [ ] task B <!-- depends: "task A" -->\n'
    )
    findings = [f for f in doctor.check() if f.fact == "dispatch.unresolved_depends"]
    assert findings and findings[0].level == doctor.OK


def test_unresolved_depends_names_every_broken_row_not_just_the_first(fleet):
    """[JUDGE MUTATION #2] truncating the scan's rows to the first must go red here:
    two independently broken `depends:` edges must both surface, or the second one
    hides silently behind the first."""
    (fleet / "repo" / "TODO.md").write_text(
        "- [ ] a task\n"
        '- [ ] first follow-up <!-- depends: "a tsak" -->\n'
        '- [ ] second follow-up <!-- depends: "another tsak" -->\n'
    )
    findings = [f for f in doctor.check() if f.fact == "dispatch.unresolved_depends"]
    assert len(findings) == 2
    assert any("a tsak" in f.detail for f in findings)
    assert any("another tsak" in f.detail for f in findings)


def test_unresolved_depends_edge_triggers_through_check_and_notify(fleet, monkeypatch):
    """The ticket's heaviest marker: announce ONCE on the transition into red, stay
    quiet on an unchanged broken set, and announce AGAIN the moment a distinct new
    reference breaks — a report every tick is a firehose nobody reads. This fact
    inherits `doctor.check_and_notify`'s edge-trigger (keyed on (fact, title)) rather
    than growing a second notification path; this test proves that inheritance
    actually holds for THIS fact, driving three explicit ticks."""
    class _StubNotify:
        def __init__(self):
            self.sent = []

        def enabled(self):
            return True

        def send(self, message, title=None):
            self.sent.append((message, title))
            return True

    stub = _StubNotify()
    monkeypatch.setattr(doctor, "notify", stub)

    # tick 1: introduce a broken reference — must announce.
    _break_unresolved_depends(fleet, monkeypatch)
    red = doctor.check_and_notify(set())
    assert len(stub.sent) == 1

    # tick 2: the SAME broken reference, unchanged — must NOT re-announce.
    red = doctor.check_and_notify(red)
    assert len(stub.sent) == 1

    # tick 3: a SECOND, distinct broken reference appears — the set changed, so this
    # must announce again.
    (fleet / "repo" / "TODO.md").write_text(
        "- [ ] a task\n"
        '- [ ] follow-up task <!-- depends: "a tsak" -->\n'
        '- [ ] another follow-up <!-- depends: "another tsak" -->\n'
    )
    doctor.check_and_notify(red)
    assert len(stub.sent) == 2


# --- installed_hooks_stale(): `chela update`'s post-update reminder reuses the exact
# comparison plugin.installed makes above, rather than a private one (CMX-170) -----------

def test_installed_hooks_stale_is_false_on_a_healthy_fleet(fleet):
    """`fleet` already installs a copy that matches what hooks_spec() renders right now."""
    assert runtime_truth.installed_hooks_stale() is False


def test_installed_hooks_stale_is_true_when_the_installed_copy_drifted(fleet, monkeypatch):
    """CMX-56, verbatim: the INSTALLED copy still kills the gate hook after 2 seconds —
    the same corruption `plugin.installed` itself catches must trip this reminder too."""
    stale = hooks.hooks_spec(PORT)
    stale["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"] = 2
    install_plugin(stale)

    assert runtime_truth.installed_hooks_stale() is True


def test_installed_hooks_stale_is_false_with_nothing_installed_at_all(tmp_path, monkeypatch):
    """No installed copy is a DIFFERENT problem (`plugin.installed` reports it loudly) —
    not something `chela update` should tell someone to `/plugin update` about."""
    claude = tmp_path / "claude"
    (claude / "plugins").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))

    assert runtime_truth.installed_hooks_stale() is False


def test_js_suites_walk_never_descends_into_git(tmp_path, monkeypatch):
    """The *.test.mjs disk walk must never ENTER .git (runtime_truth.py's own comment).

    This guards TRAVERSAL, not the returned list: `rglob("*.test.mjs")` filtered by
    `_SKIP_DIRS` afterwards returns the identical 35 files while still descending into
    .git — where, under CI's parallel workers, a concurrent ref update can make an entry
    vanish mid-walk and raise FileNotFoundError. So no assertion about the RESULT can
    catch it; only one about which directories were visited can. Restoring the
    rglob-then-filter implementation turns this red (the spy records nothing, which the
    `assert visited` below rejects as "not wired up" rather than passing vacuously).
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.test.mjs").write_text("")
    # A real non-test .mjs sibling: this repo has two (tests/e2e_interop.mjs,
    # tests/term_font_atlas_harness.mjs), so the fixture must contain one or a
    # predicate broadened from "*.test.mjs" to "*.mjs" would pass unnoticed and the
    # fact's DECLARED list would drift from what pytest actually collects.
    (tmp_path / "tests" / "harness.mjs").write_text("")
    (tmp_path / ".git" / "refs").mkdir(parents=True)
    monkeypatch.setattr(runtime_truth, "repo_root", lambda: tmp_path)

    visited: list[Path] = []
    real_walk = os.walk

    def spy(top, *args, **kwargs):
        for entry in real_walk(top, *args, **kwargs):
            visited.append(Path(entry[0]))
            yield entry

    monkeypatch.setattr(runtime_truth.os, "walk", spy)
    found = runtime_truth._js_suites_on_disk()

    assert found == ["tests/a.test.mjs"]
    assert visited, "the os.walk spy recorded nothing — the walker was never instrumented"
    assert not any(".git" in p.parts for p in visited), (
        f"the walk descended into .git: {[str(p) for p in visited]}")


# --- CMX-254: `inbox.address`'s "Fix:" line must not be a dead end --------------------
#
# `_break_inbox_address` above already pins the SEVERITY (ERROR) for a dangling/gone
# orchestrator address. These pin the remedy TEXT: "run `chela watch`" is only
# runnable from a session that is currently alive inside a tmux window — exactly the
# thing a dangling/gone address usually means is NOT true. A session restarted outside
# tmux hits that instruction, tries it, and gets `no window id` back — a second dead
# end with nothing pointing it to `chela restore`, the tool that needs no live window
# and hands back the actual fix. Live 2026-08-12: five decisions-inbox events sat
# undeliverable for about an hour behind exactly this gap.

def _inbox_declared(wid="@1", stamped="OLD-epoch", session=None, queued=2):
    return {"wid": wid, "epoch": stamped, "session": session,
            "name": "orchestrator", "queued": queued}


def test_inbox_report_dangling_names_chela_restore_as_the_fallback():
    declared = _inbox_declared()
    obs = runtime_truth.observed({"epoch": "NEW-epoch", "windows": {}})

    findings = runtime_truth._inbox_report(declared, obs)

    assert len(findings) == 1
    assert findings[0].level == runtime_truth.ERROR
    detail = findings[0].detail
    assert "chela watch" in detail
    assert "chela restore" in detail
    assert "outside tmux" in detail


def test_inbox_report_gone_names_chela_restore_as_the_fallback():
    declared = _inbox_declared(stamped="SAME-epoch")
    obs = runtime_truth.observed({"epoch": "SAME-epoch", "windows": {"@2": "chelamux"}})

    findings = runtime_truth._inbox_report(declared, obs)

    assert len(findings) == 1
    assert findings[0].level == runtime_truth.ERROR
    detail = findings[0].detail
    assert "chela watch" in detail
    assert "chela restore" in detail
