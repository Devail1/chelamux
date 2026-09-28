"""⚖️🔒 CMX-389 — the merge gate enforced INSIDE every Claude session.

`chela.mergegate` is a `PreToolUse` command hook on `Bash`: it DENIES a direct
`gh pr merge` / `gh api …/merge` / `git push` to a protected branch in a repo with a chela
workflow, and allows everything else. These tests drive the REAL hook the way Claude Code
does — the rendered plugin's command string, run by `bash -c` in a subprocess with an
explicit env, a temp `CHELA_DIR`, the payload on stdin — with no dashboard anywhere (the
gate must not depend on one). Nothing here touches `gh`, GitHub, the live `~/.chela` or
tmux: the "repos" are `git init` dirs whose remotes are never contacted.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from chela import contract, hooks, mergegate

REPO = Path(__file__).resolve().parent.parent


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def world(tmp_path):
    """A chela repo (`o/r`, base `dev`, checked out on `cmx-12`), a non-chela repo
    (`x/y`), the registry naming only the first, and a rendered plugin to run."""
    chela_repo = tmp_path / "chela-repo"
    other = tmp_path / "other-repo"
    for path, slug, branch in ((chela_repo, "o/r", "cmx-12"), (other, "x/y", "main")):
        path.mkdir()
        _git("init", "-q", "-b", branch, cwd=path)
        _git("remote", "add", "origin", f"git@github.com:{slug}.git", cwd=path)
    (chela_repo / "WORKFLOW.md").write_text("---\nproject_key: CMX\n---\n")
    state = tmp_path / "state"
    env = {"CHELA_DIR": str(state)}
    assert mergegate.register(chela_repo / "WORKFLOW.md", ["dev"], env=env) is True
    plugin = hooks.render_plugin(tmp_path / "plugin", port=5999)
    return {"chela": chela_repo, "other": other, "state": state, "env": env,
            "plugin": plugin, "tmp": tmp_path}


def _run_hook(world, command: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the plugin's OWN rendered PreToolUse/Bash command, exactly as Claude Code would:
    `bash -c <command>` with the payload on stdin and `CLAUDE_PLUGIN_ROOT` set. Explicit
    env — no inherited `CHELA_DIR`, no dashboard port that anything listens on."""
    spec = json.loads((world["plugin"] / "hooks" / "hooks.json").read_text())
    gate, = [e for e in spec["hooks"]["PreToolUse"] if e.get("matcher") == "Bash"]
    hook_cmd = gate["hooks"][0]["command"]
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command},
               "cwd": str(cwd or world["chela"]), "session_id": "s1"}
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(world["tmp"]),
           "CHELA_DIR": str(world["state"]), "CLAUDE_PLUGIN_ROOT": str(world["plugin"])}
    return subprocess.run(["bash", "-c", hook_cmd], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=30)


def _denied(proc: subprocess.CompletedProcess) -> bool:
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return False
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert "chela merge" in out["permissionDecisionReason"]
    return True


# --- the gate: every one of these is a merge, and every one is DENIED --------------------

DENIED = [
    "gh pr merge 5 --squash",
    "cd x && gh pr merge 5",
    'bash -c "gh pr merge 5"',
    "gh api -X PUT repos/o/r/pulls/5/merge",
    "git push origin HEAD:dev",
    "git push origin main",
    "git push origin +cmx-12:refs/heads/dev",
    "echo start; FOO=1 gh pr merge 5 --auto 2>&1 | tail -1",
    "gh pr merge https://github.com/o/r/pull/5",
    "gh api repos/{owner}/{repo}/pulls/5/merge --method PUT",
    "gh api graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'",
    "sh -c 'cd sub && gh pr merge 7'",
    "echo $(gh pr merge 5)",
    "git push --all origin",
]


@pytest.mark.parametrize("command", DENIED)
def test_the_hook_denies_a_merge_into_a_chela_repo(world, command):
    """🔴 GUARD: corrupt the parser (drop a verb, stop following `&&`/`bash -c`, stop reading
    the refspec's destination) and one of these merges gets through."""
    assert _denied(_run_hook(world, command)), command


# --- ⭐ the cases that must be ACCEPTED — a deny-all gate is as broken as an allow-all one --

ACCEPTED = [
    "gh pr view 5",
    "gh pr diff 5 && gh pr checks 5",
    "gh pr list --state open",
    "gh pr create --base dev --fill",
    "git push origin cmx-12",
    "git push -u origin HEAD",                # HEAD is cmx-12 in this repo
    "git push",
    'git commit -m "never run gh pr merge 5 by hand"',
    "cat > body.md <<'EOF'\ngh pr merge 5\ngit push origin dev\nEOF\nls",
    "uv run chela merge cmx-12",
    "chela merge cmx-12 --override --reason 'judge flaked'",
]


@pytest.mark.parametrize("command", ACCEPTED)
def test_the_hook_accepts_everything_that_is_not_a_merge(world, command):
    """⭐🔴 GUARD: corrupt the gate to deny-all (or to substring-match "merge"/"push") and
    these go RED — read-only `gh pr`, `gh pr create`, a feature-branch push, a heredoc that
    merely MENTIONS a merge, and `chela merge` itself."""
    assert not _denied(_run_hook(world, command)), command


def test_a_non_chela_repos_merge_is_accepted(world):
    """⭐🔴 GUARD: the gate is scoped to repos the registry names — `x/y` has no chela
    workflow, so its own merges are none of chela's business."""
    assert not _denied(_run_hook(world, "gh pr merge 5 --squash", cwd=world["other"]))
    assert not _denied(_run_hook(world, "git push origin main", cwd=world["other"]))
    assert not _denied(_run_hook(world, "gh pr merge 5 -R x/y"))


def test_the_repo_flag_wins_over_the_cwd(world):
    """`-R o/r` from anywhere is still a merge into the chela repo."""
    assert _denied(_run_hook(world, "gh pr merge 5 -R o/r", cwd=world["other"]))


def test_a_bare_push_while_the_base_branch_is_checked_out_is_denied(world):
    _git("checkout", "-q", "-b", "dev", cwd=world["chela"])
    assert _denied(_run_hook(world, "git push"))
    assert _denied(_run_hook(world, "git push origin HEAD"))


def test_a_linked_worktree_resolves_to_its_main_repo(world):
    """A dispatched agent works in a `git worktree` under `~/.chela/worktrees/…` — a
    different directory from the registered repo, which must still be recognised."""
    chela_repo = world["chela"]
    _git("-c", "user.email=t@example.com", "-c", "user.name=T", "-c", "commit.gpgsign=false",
         "commit", "-q", "--allow-empty", "-m", "seed", cwd=chela_repo)
    wt = world["tmp"] / "worktrees" / "abc"
    _git("worktree", "add", "-q", "-b", "cmx-77", str(wt), cwd=chela_repo)
    assert mergegate.repo_root(wt) == chela_repo.resolve()
    assert _denied(_run_hook(world, "gh pr merge 5", cwd=wt))
    assert not _denied(_run_hook(world, "git push origin cmx-77", cwd=wt))


def test_self_approval_of_an_override_is_denied(world):
    """An override is the OPERATOR's approval — a Claude session must not grant its own."""
    assert _denied(_run_hook(world, "uv run chela merge-approve override-abc"))
    assert _denied(_run_hook(
        world, "curl -s -X POST -d decision=approve http://127.0.0.1:5001/override/override-abc"))


# --- the dashboard is DOWN, and the gate still holds ------------------------------------

def test_the_hook_runs_with_the_dashboard_down_and_still_denies(world):
    """🔴 GUARD: the gate is a `command` hook deciding from files, NOT a curl to the
    dashboard (whose http route fails OPEN by design). Nothing listens on the rendered port
    (5999) here — and it must still deny, fast. Corrupt it into a curl relay (or gate it on
    `$CHELA_WID`, which a hand-started orchestrator session does not carry) → RED."""
    spec = json.loads((world["plugin"] / "hooks" / "hooks.json").read_text())
    gate, = [e for e in spec["hooks"]["PreToolUse"] if e.get("matcher") == "Bash"]
    command = gate["hooks"][0]["command"]
    assert "curl" not in command and "5999" not in command and "CHELA_WID" not in command
    assert gate["hooks"][0]["type"] == "command"
    proc = _run_hook(world, "gh pr merge 5 --squash")
    assert _denied(proc)


def test_the_hook_fails_open_when_it_cannot_decide_and_logs_why(world):
    """The documented trade-off: unparseable command / unreadable registry → ALLOW + log."""
    assert not _denied(_run_hook(world, "gh pr merge 5 'unbalanced"))
    log = (world["state"] / mergegate.LOG_NAME).read_text()
    assert "unparseable" in log
    (world["state"] / mergegate.REGISTRY_NAME).unlink()
    assert not _denied(_run_hook(world, "gh pr merge 5"))
    assert "no readable registry" in (world["state"] / mergegate.LOG_NAME).read_text()


def test_the_hook_logs_every_deny(world):
    _run_hook(world, "gh pr merge 5")
    lines = [json.loads(x) for x in
             (world["state"] / mergegate.LOG_NAME).read_text().splitlines()]
    assert lines[-1]["decision"] == "deny" and lines[-1]["command"] == "gh pr merge 5"


def test_main_never_raises_on_garbage(world):
    out = io.StringIO()
    assert mergegate.main(stdin=io.StringIO("not json"), stdout=out, env=world["env"]) == 0
    assert out.getvalue() == ""


def test_a_non_bash_tool_is_never_gated(world):
    payload = {"tool_name": "Write", "tool_input": {"command": "gh pr merge 5"},
               "cwd": str(world["chela"])}
    assert not mergegate.decide(payload, env=world["env"]).deny


# --- chela merge itself is untouched ------------------------------------------------------

def test_chela_merge_itself_is_not_seen_by_the_hook(world, monkeypatch):
    """`chela merge`'s own `gh pr merge` runs as a SUBPROCESS of chela's Python — an argv
    list, never a shell string through a Claude `Bash` tool — so no PreToolUse hook ever
    sees it. Pinned two ways: the gate allows the `chela merge` command line itself, and
    `_squash_merge` hands `gh` an argv list via `subprocess.run` (no shell)."""
    assert not _denied(_run_hook(world, "uv run chela merge cmx-12 --reason ok"))
    seen = []

    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kw):
        seen.append((argv, kw))
        return _Done()

    monkeypatch.setattr(contract.subprocess, "run", fake_run)
    contract._squash_merge({"task_id": "t"}, str(world["chela"]), "https://github.com/o/r/pull/5")
    argv, kw = seen[0]
    assert argv[:4] == ["gh", "pr", "merge", "5"] and not kw.get("shell")


# --- the plugin ships the script, byte-for-byte -------------------------------------------

def test_the_committed_plugin_ships_the_current_gate_script():
    committed = REPO / "plugin" / "hooks" / hooks.MERGEGATE_SCRIPT
    assert committed.read_text() == hooks.mergegate_source()


def test_the_gate_script_is_stdlib_only():
    """It runs from Claude Code's plugin cache with `python3 -I -S`, where chela is not
    importable — one `from chela import …` and every session's gate silently fails open."""
    import ast
    import sys
    tree = ast.parse(hooks.mergegate_source())
    modules = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
               for a in n.names}
    modules |= {n.module.split(".")[0] for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module}
    assert modules and modules <= set(sys.stdlib_module_names), modules


def test_the_forbidden_bases_agree_with_the_contract():
    assert mergegate.FORBIDDEN_BASES == contract.FORBIDDEN_BASES


def test_the_gate_rides_behind_the_ingestion_hook():
    pre = hooks.hooks_spec(port=5001)["hooks"]["PreToolUse"]
    assert pre[0]["matcher"] == "*" and "curl" in pre[0]["hooks"][0]["command"]
    assert pre[1] == hooks.mergegate_entry()
    assert pre[1]["matcher"] == "Bash"


# --- the registry the gate decides from ---------------------------------------------------

def test_register_writes_once_and_is_idempotent(world):
    env = world["env"]
    assert mergegate.register(world["chela"] / "WORKFLOW.md", ["dev"], env=env) is False
    entry, = mergegate.load_registry(env)
    assert entry["slugs"] == ["o/r"] and entry["bases"] == ["dev"]
    assert entry["repo_dir"] == str(world["chela"].resolve())
    assert mergegate.register(world["chela"] / "WORKFLOW.md", ["trunk"], env=env) is True
    entry, = mergegate.load_registry(env)
    assert entry["bases"] == ["trunk"]


def test_a_declared_base_other_than_dev_is_protected(world):
    mergegate.register(world["chela"] / "WORKFLOW.md", ["trunk"], env=world["env"])
    assert _denied(_run_hook(world, "git push origin HEAD:trunk"))
    assert not _denied(_run_hook(world, "git push origin HEAD:dev"))
