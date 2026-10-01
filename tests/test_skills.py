"""CMX-415 — the skills under ``skills/`` are instructions an agent will RUN.

A skill that names a removed subcommand or flag fails only when an agent follows it, which
is the worst place to find out: ``orchestrate`` went two months without ``chela merge``
while the CLI moved on. Two guards:

* every ``chela <sub> … --flag`` a SKILL.md tells the reader to run resolves against the
  CURRENT CLI's argparse tree (``chela.main.main`` builds it inline, so the parser is
  captured at its ``parse_args`` call rather than duplicated here);
* every SKILL.md has YAML frontmatter with a ``name`` (its directory) and a ``description``.

The private-strings sweep over ``skills/`` lives with the other public files in
``tests/test_public_media.py``.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from unittest import mock

import pytest
import yaml

from chela import main as chela_main

ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))

_FENCE_RE = re.compile(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", re.S | re.M)  # list-indented too
_INLINE_RE = re.compile(r"`([^`\n]+)`")
# `chela` in COMMAND position — the start of a line or span, after `uv run`, a chain/pipe
# operator, `$(`, or an opening backtick (a span inside a fenced prose template) — so the
# word in a quoted message ("chela is wired…") or in `chela-setup`/`chela.env` is not one.
_INVOCATION_RE = re.compile(r"(?:^|&&|\|\||[;|`(]|\buv run)\s*chela[ \t]+")
# Where an invocation's own argv ends inside a line: a chain, a pipe, a comment, a closing
# backtick (a span inside a fenced prose template), or an arrow of explanation.
_ARGV_END_RE = re.compile(r"&&|\|\||[;|`→]|\s#")
_WORD_RE = re.compile(r"^[a-z](?:[a-z-]*[a-z])?$")
_FLAG_RE = re.compile(r"^(--?[A-Za-z][\w-]*)(?:=.*)?$")


class _ParserCaptured(Exception):
    pass


def _cli_parser() -> argparse.ArgumentParser:
    """The real top-level parser, grabbed at the moment ``main()`` would parse argv."""
    def grab(self, *args, **kwargs):
        raise _ParserCaptured(self)

    with mock.patch.object(argparse.ArgumentParser, "parse_args", grab):
        try:
            chela_main.main()
        except _ParserCaptured as got:
            return got.args[0]
    raise AssertionError("chela.main.main() never reached parse_args")


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _has_positionals(parser: argparse.ArgumentParser) -> bool:
    return any(not a.option_strings and not isinstance(a, argparse._SubParsersAction)
               for a in parser._actions)


def chela_invocations(markdown: str) -> list[list[str]]:
    """The argv (after ``chela``) of every command the markdown hands the reader."""
    lines = [ln for block in _FENCE_RE.findall(markdown) for ln in block.splitlines()]
    prose = _FENCE_RE.sub("", markdown)
    lines += _INLINE_RE.findall(prose)
    out = []
    for line in lines:
        for m in _INVOCATION_RE.finditer(line):
            argv = _ARGV_END_RE.split(line[m.end():], maxsplit=1)[0].split()
            if argv:
                out.append(argv)
    return out


def check_invocation(parser: argparse.ArgumentParser, argv: list[str]) -> tuple[str, str | None]:
    """``(resolved subcommand path, error or None)`` for one ``chela …`` argv.

    An argv whose first token is not a command word (a placeholder like ``…``) resolves to
    ``""`` and is not an error — it names no subcommand to check.
    """
    if not _WORD_RE.match(argv[0]):
        return "", None
    path: list[str] = []
    i = 0
    while i < len(argv):
        subs = _subparsers(parser)
        if not subs:
            break
        tok = argv[i]
        if tok in subs:
            parser = subs[tok]
            path.append(tok)
            i += 1
            continue
        if _WORD_RE.match(tok) and not _has_positionals(parser):
            where = " ".join(["chela", *path])
            return " ".join(path), f"{where}: no subcommand {tok!r} (have: {sorted(subs)})"
        break
    known = parser._option_string_actions
    for tok in argv[i:]:
        m = _FLAG_RE.match(tok)
        if m and m.group(1) not in known:
            return " ".join(path), f"chela {' '.join(path)}: no flag {m.group(1)!r}"
    return " ".join(path), None


@pytest.fixture(scope="module")
def parser():
    return _cli_parser()


def test_skills_exist():
    names = {p.parent.name for p in SKILLS}
    assert {"orchestrate", "chela-setup", "telegram-setup", "telegram-send", "handoff"} <= names


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_every_chela_command_in_a_skill_exists_in_the_cli(parser, skill):
    errors = []
    for argv in chela_invocations(skill.read_text(encoding="utf-8")):
        _, err = check_invocation(parser, argv)
        if err:
            errors.append(f"`chela {' '.join(argv)}` → {err}")
    assert not errors, f"{skill.relative_to(ROOT)} tells the reader to run:\n" + "\n".join(errors)


def test_the_skills_teach_the_commands_the_workflow_runs_on(parser):
    """The extraction must actually SEE the load-bearing commands — an extractor that
    finds nothing would pass the check above on every skill."""
    seen = {check_invocation(parser, argv)[0]
            for skill in SKILLS for argv in chela_invocations(skill.read_text(encoding="utf-8"))}
    required = {"merge", "review", "reopen", "retry", "wait", "dispatch", "telegram",
                "request-push", "task-finished", "doctor", "judge self-check", "escalate"}
    assert required <= seen, f"skills no longer teach: {sorted(required - seen)}"


@pytest.mark.parametrize("markdown,accepted", [
    ("run `chela merge cmx-1 --override --reason x`", True),
    ("```bash\nchela dispatch --pause --reason r   # hold\n```", True),
    ("`chela judge show cmx-1 --held-out`", True),
    ("`chela …` on your PATH", True),
    ("run `chela mergee cmx-1`", False),                     # removed subcommand
    ("run `chela merge cmx-1 --force`", False),              # removed flag
    ("```\nchela judge rerun cmx-1\n```", False),            # unknown sub-subcommand
    ("```\nchela dispatch --pause && chela wait x --untill done\n```", False),
])
def test_the_command_check_accepts_real_commands_and_rejects_fakes(parser, markdown, accepted):
    argvs = chela_invocations(markdown)
    assert argvs, f"no invocation extracted from {markdown!r}"
    errors = [err for argv in argvs if (err := check_invocation(parser, argv)[1])]
    assert (not errors) is accepted, (markdown, errors)


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path.relative_to(ROOT)}: no YAML frontmatter block at the top"
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        # Claude Code also reads the flat `key: value` form whose value holds an unquoted
        # ": " (strict YAML refuses it) — the third-party skills here are written that way.
        # Every unindented line must still be a `key: value` line.
        data = {}
        for line in m.group(1).splitlines():
            if not line.strip() or line[0].isspace():
                continue
            kv = re.match(r"^([A-Za-z][\w-]*):[ \t]*(.*)$", line)
            assert kv, f"{path.relative_to(ROOT)}: frontmatter line is not `key: value`: {line!r}"
            data[kv.group(1)] = kv.group(2)
    assert isinstance(data, dict), f"{path.relative_to(ROOT)}: frontmatter is not a mapping"
    return data


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_every_skill_has_valid_frontmatter(skill):
    data = _frontmatter(skill)
    assert data.get("name") == skill.parent.name, (
        f"{skill.relative_to(ROOT)}: frontmatter name {data.get('name')!r} must be the "
        f"skill's directory name {skill.parent.name!r}")
    desc = data.get("description")
    assert isinstance(desc, str) and desc.strip(), (
        f"{skill.relative_to(ROOT)}: frontmatter needs a non-empty description")
