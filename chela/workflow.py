from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_KEY_RE = re.compile(r"^[A-Z]{2,5}$")


@dataclass
class WorkflowDef:
    path: Path
    config: dict
    prompt_template: str

    def get(self, *keys, default=None):
        cur: object = self.config
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    @property
    def project_key(self) -> str:
        # Validated at load time, so this is guaranteed to be present and well-formed.
        return self.config["project_key"]


def _expand(value):
    if not isinstance(value, str):
        return value
    if value.startswith("$"):
        return os.environ.get(value[1:], "")
    return os.path.expandvars(os.path.expanduser(value))


def load_workflow(path: str | Path) -> WorkflowDef:
    p = Path(path).expanduser().resolve()
    text = p.read_text()

    config: dict = {}
    body = text

    if text.startswith("---\n") or text.startswith("---\r\n"):
        end = text.find("\n---", 4)
        if end == -1:
            raise ValueError(f"{p}: unterminated YAML front matter")
        front = text[4:end]
        body = text[end + 4:]
        parsed = yaml.safe_load(front) or {}
        if not isinstance(parsed, dict):
            raise ValueError(f"{p}: front matter must be a YAML map")
        config = parsed

    pk = config.get("project_key")
    if not isinstance(pk, str) or not PROJECT_KEY_RE.match(pk):
        raise ValueError(
            f"{p}: front matter is missing a valid `project_key` "
            f"(must match {PROJECT_KEY_RE.pattern!r}, e.g. `project_key: PCLW`); got {pk!r}"
        )

    return WorkflowDef(path=p, config=config, prompt_template=body.strip())


def render_prompt(template: str, vars: dict) -> str:
    out = template
    for k, v in vars.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def resolve_workspace_root(wf: WorkflowDef) -> Path:
    root = wf.get("workspace", "root", default="~/.picoclaw/worktrees/default")
    root = _expand(root)
    p = Path(root)
    if not p.is_absolute():
        p = (wf.path.parent / p).resolve()
    return p
