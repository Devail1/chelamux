"""🪆 CMX-190 — a dispatched run may not launch another Claude Code session.

The merge-gate hook (`chela.mergegate`) denies a `Bash` command that starts `claude`, however
it is wrapped, when the session carries `$CHELA_RUN_ID` (set by the dispatcher at spawn).
Fixture payloads only: the hook decides, nothing here ever runs `claude`.
"""
from __future__ import annotations

import io
import json

import pytest

from chela import mergegate

DISPATCHED = {"CHELA_RUN_ID": "CMX-117", "CHELA_RUN_ROLE": "coding"}
HUMAN = {"CHELA_WID": "@5"}
DENY_TEXT = ("Launching another Claude agent from a dispatched run is not allowed. Don't retry "
             "it in another mode or behind a wrapper. Reproduce with a stub process, or hand "
             "it to the orchestrator.")

LAUNCHES = [
    "claude",
    "claude -p 'hi'",
    "claude --print hi",
    "claude --resume abc",
    "claude --dangerously-skip-permissions",
    "claude --permission-mode plan",
    'tmux -L cmx117 new-session -d -s repro "claude --dangerously-skip-permissions"',
    "tmux -L cmx117 new -d 'claude --permission-mode plan'",
    "tmux new-window -n x claude",
    "tmux -S /tmp/s split-window -t x:0 'claude -p hi'",
    "tmux respawn-pane -k -t x 'claude'",
    "tmux send-keys -t x 'claude' Enter",
    "env -u CHELA_RUN_ID claude -p hi",
    "env FOO=1 claude",
    "FOO=1 claude",
    "nohup claude >/dev/null 2>&1 &",
    "setsid claude",
    "timeout 30 claude -p hi",
    "timeout -k 5 30s claude",
    "sudo -u bob claude",
    'bash -c "cd /x && claude -p hi"',
    "sh -lc 'claude'",
    "eval claude -p hi",
    "echo $(claude -p hi)",
    "cd /x && /usr/local/bin/claude",
    "~/.local/bin/claude --resume x",
    "npx @anthropic-ai/claude-code",
    "npx -y @anthropic-ai/claude-code@1.2.3 -p hi",
    "pnpm dlx @anthropic-ai/claude-code",
    "bunx @anthropic-ai/claude-code --permission-mode plan",
    "claude --version --dangerously-skip-permissions",
    "claude plugin",
    "claude mcp add x",
    # Malformed (unbalanced quote) but clearly a launch: fails CLOSED.
    "tmux new-session \"claude --dangerously-skip-permissions",
    "bash -c 'claude -p hi",
]

ALLOWED = [
    "claude --version",
    "claude plugin test plugin/",
    "claude plugin validate plugin/",
    "claude mcp list",
    "npx @anthropic-ai/claude-code --version",
    "grep -r claude-code .",
    "cat CLAUDE.md",
    "ls ~/.claude/",
    "npm install -g @anthropic-ai/claude-code",
    "tmux -L x new-session -d -s claude",
    "timeout 5 grep -rn foo .",
    "echo 'unbalanced CLAUDE.md",
    "",
]


def _decide(command: str, env: dict) -> mergegate.Decision:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/"}
    return mergegate.decide(payload, env=env, registry=[])


@pytest.mark.parametrize("command", LAUNCHES)
def test_a_launch_is_denied_in_a_dispatched_run(command):
    d = _decide(command, DISPATCHED)
    assert d.deny and DENY_TEXT in d.reason


@pytest.mark.parametrize("command", LAUNCHES)
def test_a_launch_is_allowed_in_a_human_session(command):
    assert not _decide(command, HUMAN).deny
    assert not _decide(command, {**DISPATCHED, "CHELA_RUN_ID": ""}).deny


@pytest.mark.parametrize("command", ALLOWED)
def test_allowlisted_and_non_launch_commands_pass_a_dispatched_run(command):
    assert not _decide(command, DISPATCHED).deny


def test_too_deep_nesting_fails_closed_on_a_claude_word():
    assert mergegate.claude_launch("bash -c 'claude --version'", depth=4)
    assert not mergegate.claude_launch("bash -c 'cat CLAUDE.md'", depth=4)


def test_the_hook_entry_point_writes_the_deny():
    """`main` (the script the plugin runs) emits the PreToolUse deny on stdout."""
    payload = {"tool_name": "Bash", "tool_input": {"command": "claude -p x"}, "cwd": "/"}
    out = io.StringIO()
    env = {**DISPATCHED, "CHELA_DIR": "/nonexistent-chela-dir"}
    assert mergegate.main(stdin=io.StringIO(json.dumps(payload)), stdout=out, env=env) == 0
    spec = json.loads(out.getvalue())["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny" and DENY_TEXT in spec["permissionDecisionReason"]
