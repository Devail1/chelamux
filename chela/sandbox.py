"""🧱 The sandbox boundary for dispatched agents (issue #502, ``docs/SANDBOX_BOUNDARY.md``).

That document is the brief: it enumerates what a coding agent, a rework agent and the
judge legitimately write and reach, measures what the stock Claude Code sandbox already
gives for free, and finds two blockers that had to move to the daemon before turning the
sandbox on would be more than cosmetic (CMX-366's run-row write, CMX-368's push/PR-open —
both landed). §6 is what is left: ship §6.3's config, behind a per-workflow flag, for the
**coding and rework roles only** — the judge stays unsandboxed, decided 2026-09-14 (§6.1),
because it runs a fixed command it cannot choose, in a throwaway tree, and never pushes,
while a coding agent takes an arbitrary task description and pushes to a public repo.

This module owns exactly that: the frozen §6.3 config, where its file lives (§6.2), and
the one check (``role``) that keeps it off the judge.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from chela.transcripts import claude_config_dir
from chela.workflow import WorkflowDef

log = logging.getLogger(__name__)

# ⛔ Not `settings.json` — that name is Claude Code's own, and Claude Code deliberately
# ignores the restricted keys (`mask`, `tlsTerminate`, `strictAllowlist`,
# `filesystem.disabled`) from a *project* settings file, while the operator's own
# `~/.claude/settings.json` would sandbox their interactive sessions too (§6.2). This is a
# distinct, chela-owned file, in the one directory that stays read-only from inside the
# very sandbox it configures (§6.2's measured note: it is read-only from inside as long as
# it is not itself under a granted path — `~/.claude` is never in `filesystem.allowWrite`
# below).
SANDBOX_SETTINGS_FILENAME = "chela-sandbox.settings.json"

# §6.3, verbatim. Frozen to what was measured (§2/§5's probes) — widen it only against a
# fresh measurement, not by editing this dict.
SANDBOX_SETTINGS: dict = {
    "sandbox": {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        # ⛔ load-bearing: without it Claude retries a blocked command with
        # dangerouslyDisableSandbox and the boundary is advisory, not enforced.
        "allowUnsandboxedCommands": False,
        "filesystem": {
            "allowWrite": ["~/.cache/uv", "~/.local/share/uv"],
        },
        "network": {
            # ⛔ also load-bearing: without it an unlisted host PROMPTS, and a dispatched
            # agent has nobody to answer — the dispatcher finds it hung.
            "strictAllowlist": True,
            "allowedDomains": [
                "github.com", "*.github.com",
                "pypi.org", "files.pythonhosted.org",
                "registry.npmjs.org",
            ],
        },
        # `deny` rather than `mask` (§6.3's note): B2 is closed by moving the push and the
        # PR open to the daemon (CMX-368), so no sandboxed command needs the token any
        # more, and `deny` is simpler and stronger than a mask whose substring
        # substitution only ever covered one of `gh`/`git`'s two auth encodings.
        "credentials": {
            "files": [
                {"path": "~/.config/gh/hosts.yml", "mode": "deny"},
            ],
        },
    },
}


def sandbox_settings_path() -> Path:
    """Where the chela-owned sandbox settings file lives — ``<claude config dir>/
    chela-sandbox.settings.json``. Uses :func:`chela.transcripts.claude_config_dir` (not a
    hardcoded ``~/.claude``) so an operator who relocates Claude Code's config dir via
    ``CLAUDE_CONFIG_DIR`` gets the boundary in the same place Claude Code itself looks for
    ``--settings`` files, per §6.2."""
    return claude_config_dir() / SANDBOX_SETTINGS_FILENAME


def ensure_sandbox_settings_file() -> Path:
    """Write :data:`SANDBOX_SETTINGS` to :func:`sandbox_settings_path`, creating the
    directory if needed, and return the path. Skips the write when the file already holds
    this exact content, so a dispatcher polling every tick does not rewrite it on every
    launch."""
    path = sandbox_settings_path()
    text = json.dumps(SANDBOX_SETTINGS, indent=2) + "\n"
    try:
        if path.read_text() == text:
            return path
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def sandbox_enabled(wf: WorkflowDef) -> bool:
    """§6.4 step 3 — ship §6.3 behind a per-workflow flag. Off unless a workflow opts in
    with ``agent.sandbox: true`` in its ``WORKFLOW.md``; unset/false changes nothing for a
    workflow that has not adopted it."""
    return bool(wf.get("agent", "sandbox", default=False))


def sandbox_launch_arg(wf: WorkflowDef, role: str) -> str | None:
    """The ``--settings <path>`` flag to launch ``role`` with, or ``None``.

    §6.1 (decided 2026-09-14): sandbox the coding and rework agents, never the judge.
    ``_launch_agent``'s two coding-role callers (the first dispatch and the rework
    re-spawn) both pass the default ``role="coding"``; the judge is the only caller that
    passes ``role="judge"`` — so that one check is the whole boundary between "benefits
    from the sandbox" and "runs a fixed command in a throwaway tree and never pushes".
    Returns None outright for the judge, before even checking whether the workflow opted
    in, so a workflow that turns this on can never accidentally sandbox its judge by
    misconfiguring the flag.
    """
    if role == "judge":
        return None
    if not sandbox_enabled(wf):
        return None
    path = ensure_sandbox_settings_file()
    return f"--settings {path}"
