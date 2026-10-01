"""The model call, behind one small interface so every test runs without it.

:class:`ClaudeCLIRunner` drives Claude Code headless (``claude -p``) — the same harness the
live judge runs in, so the design step sees the same tool behaviour, and no API SDK or key
handling is added to chela. It is locked down for an eval:

* ``--restricted --tools Read,Grep,Glob`` (design) or ``--tools ""`` (grader): nothing that
  runs a command, edits a file or reaches the network; file tools confined to the cwd, and
  user/project settings (hooks, plugins) ignored;
* ``--strict-mcp-config``, ``--no-session-persistence``, ``--permission-prompts none``;
* ``--max-budget-usd`` per call, on top of the eval's own total cap;
* a child env with pm2's leaked vars stripped (``envutil.child_env``) and every ``CHELA_*``
  var removed, so no chela hook mistakes an eval call for a dispatched run.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from chela import envutil


@dataclass
class RunResult:
    structured: dict | None
    cost_usd: float = 0.0
    error: str = ""


class ClaudeCLIRunner:
    def __init__(self, model: str, *, max_budget_usd: float = 3.0, timeout: float = 1200.0,
                 effort: str | None = None, binary: str = "claude"):
        self.model = model
        self.max_budget_usd = max_budget_usd
        self.timeout = timeout
        self.effort = effort
        self.binary = binary

    def command(self, prompt: str, schema: dict, tools: tuple[str, ...]) -> list[str]:
        cmd = [self.binary, "-p", prompt, "--output-format", "json", "--model", self.model,
               "--restricted", "--tools", ",".join(tools), "--strict-mcp-config",
               "--no-session-persistence", "--permission-prompts", "none",
               "--max-budget-usd", f"{self.max_budget_usd:.2f}",
               "--json-schema", json.dumps(schema)]
        if self.effort:
            cmd += ["--effort", self.effort]
        return cmd

    @staticmethod
    def env() -> dict[str, str]:
        env = envutil.child_env()
        for k in list(env):
            if k.startswith("CHELA_") or k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
                env.pop(k)
        return env

    def run(self, prompt: str, *, cwd: str | Path | None, schema: dict,
            tools: tuple[str, ...] = ("Read", "Grep", "Glob")) -> RunResult:
        try:
            out = subprocess.run(self.command(prompt, schema, tools), cwd=cwd,
                                 capture_output=True, text=True, errors="replace",
                                 timeout=self.timeout, env=self.env(), stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            return RunResult(None, 0.0, f"the model call did not complete: {e}")
        try:
            data = json.loads(out.stdout or "{}")
        except ValueError:
            return RunResult(None, 0.0, f"unparseable CLI output (exit {out.returncode}): "
                                        f"{(out.stderr or out.stdout).strip()[:300]}")
        cost = float(data.get("total_cost_usd") or 0.0)
        if data.get("is_error") or out.returncode != 0:
            return RunResult(None, cost, f"model call failed ({data.get('subtype') or out.returncode}): "
                                         f"{str(data.get('result') or out.stderr)[:300]}")
        structured = data.get("structured_output")
        if not isinstance(structured, dict):
            return RunResult(None, cost, "the model returned no structured output")
        return RunResult(structured, cost, "")
