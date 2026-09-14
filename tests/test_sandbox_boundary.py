"""🧱 issue #502 / docs/SANDBOX_BOUNDARY.md §6 — the sandbox boundary for the coding and
rework roles, shipped behind a per-workflow flag, never applied to the judge.

CMX-366 (the run-row write) and CMX-368 (the push/PR-open) already moved the two
privileged effects Done Criteria step 6 needs off the agent's own process — this is what
was left: §6.3's config, written to a chela-owned file (§6.2), and wired into
``_launch_agent`` so a workflow that opts in via ``agent.sandbox: true`` gets it on the
coding and rework roles, and the judge never does.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from chela import sandbox
from chela.workflow import WorkflowDef


def _wf(tmp_path, **agent):
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"project_key": "CMX", "agent": agent},
        prompt_template="go {{workspace_path}}",
    )


def _conn(dispatcher):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return dispatcher.ensure_schema(conn)


def _capture_send_keys(monkeypatch, dispatcher, *, window_id="@100"):
    sent: list[str] = []

    def fake_run(argv, *a, **k):
        if argv[:2] == ["tmux", "new-window"]:
            return SimpleNamespace(stdout=f"{window_id}\n", returncode=0)
        if argv[:2] == ["tmux", "send-keys"] and len(argv) > 4:
            sent.append(argv[4])
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    return sent


# --- chela.sandbox unit-level ------------------------------------------------------

def test_sandbox_settings_path_lives_under_the_claude_config_dir(monkeypatch, tmp_path):
    """§6.2: the file is chela-owned, never `settings.json` (that name is Claude Code's
    own, and a project one has its restricted keys ignored while the operator's own would
    sandbox their interactive sessions too)."""
    claude_dir = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    path = sandbox.sandbox_settings_path()
    assert path == claude_dir / "chela-sandbox.settings.json"
    assert path.name != "settings.json"


def test_ensure_sandbox_settings_file_writes_the_measured_602_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    path = sandbox.ensure_sandbox_settings_file()
    assert path.exists()
    on_disk = json.loads(path.read_text())
    assert on_disk == sandbox.SANDBOX_SETTINGS


# §6.3 is a frozen contract ("verbatim. Frozen to what was measured — widen it only
# against a fresh measurement") — the test above compares the on-disk file against
# `sandbox.SANDBOX_SETTINGS`, the SAME object it was serialized from, so it is identity
# under the comparison and can never disagree with itself: it catches a serialization bug,
# never a change to the dict's content (docs/defeat_shapes/369).
# This is the independently-written literal that closes that gap — edit it only against a
# fresh §6 measurement, never to make a code change pass.
_EXPECTED_SANDBOX_SETTINGS = {
    "sandbox": {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "filesystem": {
            "allowWrite": ["~/.cache/uv", "~/.local/share/uv"],
        },
        "network": {
            "strictAllowlist": True,
            "allowedDomains": [
                "github.com", "*.github.com",
                "pypi.org", "files.pythonhosted.org",
                "registry.npmjs.org",
            ],
        },
        "credentials": {
            "files": [
                {"path": "~/.config/gh/hosts.yml", "mode": "deny"},
            ],
        },
    },
}


def test_sandbox_settings_dict_matches_the_frozen_602_literal_exactly():
    """§6.3's whole-dict change detector: `SANDBOX_SETTINGS` compared against a literal
    written out HERE, independently of `chela.sandbox`, so any edit to ANY leaf —
    `enabled` flipped off, a path added to `allowWrite`, a host added to `allowedDomains`,
    `allowUnsandboxedCommands` flipped, a `mask` swapped in for `deny` — turns this test
    red, not just the leaves an earlier round happened to spot-check individually."""
    assert sandbox.SANDBOX_SETTINGS == _EXPECTED_SANDBOX_SETTINGS


def test_ensure_sandbox_settings_file_is_idempotent(tmp_path, monkeypatch):
    """A dispatcher tick calling this on every launch must not rewrite an unchanged file —
    same shape as workflow.py's stat-before-parse gate."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    first = sandbox.ensure_sandbox_settings_file()
    mtime_before = first.stat().st_mtime_ns
    second = sandbox.ensure_sandbox_settings_file()
    assert second == first
    assert second.stat().st_mtime_ns == mtime_before


def test_ensure_sandbox_settings_file_refreshes_stale_content(tmp_path, monkeypatch):
    """The negative control the idempotency test above is only the positive half of
    (docs/defeat_shapes/369d): an existing file must be refreshed when its content is
    STALE, not merely left alone because it exists. A chela upgrade that narrows
    `allowWrite`, adds a `credentials` entry, or flips a leaf must reach every operator's
    `~/.claude` on the next launch, not sit unapplied forever because the file was already
    there from before the upgrade."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    path = sandbox.sandbox_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sandbox": {"enabled": False}}))

    result = sandbox.ensure_sandbox_settings_file()

    assert result == path
    assert json.loads(path.read_text()) == sandbox.SANDBOX_SETTINGS


def test_allow_unsandboxed_commands_is_false():
    """⛔ Load-bearing per §6.3: without it, a blocked command retries with
    dangerouslyDisableSandbox and the boundary is advisory, not enforced."""
    assert sandbox.SANDBOX_SETTINGS["sandbox"]["allowUnsandboxedCommands"] is False


def test_network_strict_allowlist_is_true():
    """⛔ Also load-bearing per §6.3: without it an unlisted host PROMPTS, and a dispatched
    agent — unattended, nobody to answer — is found hung by the dispatcher."""
    assert sandbox.SANDBOX_SETTINGS["sandbox"]["network"]["strictAllowlist"] is True


def test_credentials_deny_the_github_token_file():
    """§6.3's `deny` (not `mask`) on the token file — B2 is closed by moving the push and
    PR-open to the daemon (CMX-368), so no sandboxed command needs the token at all."""
    files = sandbox.SANDBOX_SETTINGS["sandbox"]["credentials"]["files"]
    assert {"path": "~/.config/gh/hosts.yml", "mode": "deny"} in files


def test_sandbox_disabled_by_default(tmp_path, monkeypatch):
    """§6.4 step 3: the per-workflow flag is OFF unless a workflow opts in — a workflow
    that has not adopted `agent.sandbox` must see NO change in its launch command, which
    includes writing NO settings file as a side effect (docs/defeat_shapes/369c)."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    wf = _wf(tmp_path)
    assert sandbox.sandbox_enabled(wf) is False
    assert sandbox.sandbox_launch_arg(wf, "coding") is None
    assert not sandbox.sandbox_settings_path().exists()


def test_sandbox_launch_arg_never_fires_for_the_judge_even_when_enabled(tmp_path, monkeypatch):
    """§6.1 (decided 2026-09-14): sandbox coding + rework, never the judge — this is the
    ONE check the whole boundary between them rests on. The judge call must also launch
    byte-identically to an unsandboxed one, which includes writing NO settings file as a
    side effect (docs/defeat_shapes/369c) — only the later opted-in coding call may
    materialize it."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    wf = _wf(tmp_path, sandbox=True)
    assert sandbox.sandbox_launch_arg(wf, "judge") is None
    assert not sandbox.sandbox_settings_path().exists()
    assert sandbox.sandbox_launch_arg(wf, "coding") is not None


def test_sandbox_launch_arg_materializes_the_settings_file(tmp_path, monkeypatch):
    """docs/defeat_shapes/369b: a mutation that swaps `ensure_sandbox_settings_file()` for
    the bare `sandbox_settings_path()` returns the IDENTICAL path string — the
    `--settings <path>` flag looks unchanged — but never writes the file, so the sandbox
    silently never applies. Assert the file exists on disk with the frozen content, not
    just that the returned string names the right path."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    wf = _wf(tmp_path, sandbox=True)
    path = sandbox.sandbox_settings_path()
    assert not path.exists()

    arg = sandbox.sandbox_launch_arg(wf, "coding")

    assert arg == f"--settings {path}"
    assert path.exists()
    assert json.loads(path.read_text()) == sandbox.SANDBOX_SETTINGS


# --- wired into _launch_agent -------------------------------------------------------

def test_launch_agent_carries_settings_flag_when_workflow_opts_in(monkeypatch, tmp_path):
    import chela.dispatcher as dispatcher

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    sent = _capture_send_keys(monkeypatch, dispatcher)

    wf = _wf(tmp_path, sandbox=True)
    conn = _conn(dispatcher)
    dispatcher._launch_agent(
        wf, "t1", "cmx-1", tmp_path / "wt", "go", conn,
        hook_vars={}, fresh_worktree=False,
    )

    claude_line = next(line for line in sent if line.startswith("claude"))
    expected = sandbox.sandbox_settings_path()
    assert f"--settings {expected}" in claude_line
    # Not just the flag string: the file it points at must actually exist, or the
    # sandbox never applies (docs/defeat_shapes/369b).
    assert expected.exists()
    assert json.loads(expected.read_text()) == sandbox.SANDBOX_SETTINGS


def test_launch_agent_omits_settings_flag_when_workflow_has_not_opted_in(monkeypatch, tmp_path):
    import chela.dispatcher as dispatcher

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    sent = _capture_send_keys(monkeypatch, dispatcher)

    wf = _wf(tmp_path)
    conn = _conn(dispatcher)
    dispatcher._launch_agent(
        wf, "t1", "cmx-1", tmp_path / "wt", "go", conn,
        hook_vars={}, fresh_worktree=False,
    )

    claude_line = next(line for line in sent if line.startswith("claude"))
    assert "--settings" not in claude_line
    assert not sandbox.sandbox_settings_path().exists()


def test_spawn_judge_never_carries_the_settings_flag_even_when_the_workflow_opts_in(
    monkeypatch, tmp_path
):
    """`_spawn_judge` calls `_launch_agent` with `role="judge"` — the SAME opted-in
    workflow that sandboxes its coding/rework agents must still launch an unsandboxed
    judge (§6.1)."""
    import chela.dispatcher as dispatcher

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    sent = _capture_send_keys(monkeypatch, dispatcher)

    wf = _wf(tmp_path, sandbox=True)
    conn = _conn(dispatcher)
    conn.execute(
        "INSERT INTO runs (task_id, workflow_path, title, status, branch_name, "
        "task_number, pr_url) VALUES (?, ?, ?, 'awaiting_review', ?, ?, ?)",
        ("abc123", str(wf.path), "do a thing", "cmx-1", 1, "https://x/pull/1"),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM runs WHERE task_id=?", ("abc123",)).fetchone()

    monkeypatch.setattr(dispatcher, "detached_worktree",
                         lambda *_a, **_k: (tmp_path / "judge-abc123", True))
    assert dispatcher._spawn_judge(wf, row, "deadbeef", conn) is True

    claude_line = next(line for line in sent if line.startswith("claude"))
    assert "--settings" not in claude_line
