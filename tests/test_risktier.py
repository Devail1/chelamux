"""⚖️🪜 CMX-208 — the merge gate's risk tiers: prose / always-human / judge."""
from __future__ import annotations

from pathlib import Path

import pytest

from chela import risktier
from chela.workflow import WorkflowDef

D = risktier.DEFAULT_ALWAYS_HUMAN
PROSE = ["docs/guide.md", "docs/a/b/page.rst", "docs/judge_eval/x.jsonl", "docs/notes.txt",
         "changelog.d/CMX-208.md", "README.md", "docs/media/demo.MP4", "chela/assets/logo.png",
         "plugin.md"]
NOT_PROSE = ["docs/helper.py", "docs/run", "changelog.d/sub/x.md", "WORKFLOW.md", "CLAUDE.md",
             "AGENTS.md", "chela/README.md", "requirements.txt", "LICENSE", "xdocs/a.md",
             "app.py", "README.md.py", "chela/contract.pyi", "docs/CLAUDE.md",
             "docs/sub/AGENTS.md", "docs/WORKFLOW.md", "changelog.d/CLAUDE.md"]
HUMAN = ["WORKFLOW.md", ".github/workflows/ci.yml", "plugin/hooks/hooks.json",
         "chela/mergegate.py", "chela/contract.py", "chela/risktier.py", "chela/release.py",
         "scripts/release-cut.sh"]


@pytest.mark.parametrize("name", PROSE + NOT_PROSE)
def test_is_prose(name):
    assert risktier.is_prose(name) is (name in PROSE)


@pytest.mark.parametrize("path", HUMAN)
def test_each_default_always_human_path_wins_over_prose(path):
    assert risktier.classify(["docs/x.md", path], D) == ("human", path)


@pytest.mark.parametrize("files", [["docs/secret/a.md"], ["docs/x.md", "docs/secret/a.md"]])
def test_an_always_human_path_wins_even_when_it_is_itself_prose(files):
    assert all(risktier.is_prose(f) for f in files)
    assert risktier.classify(files, D + ("docs/secret/**",)) == ("human", "docs/secret/a.md")
    assert risktier.classify(files, D) == ("prose", None)    # negative control: not added


@pytest.mark.parametrize("files, tier", [
    (PROSE, "prose"), (["docs/x.md", "app.py"], "judge"), (None, "judge"), ([], "judge"),
])
def test_classify(files, tier):
    assert risktier.classify(files, D) == (tier, None)


@pytest.mark.parametrize("merge, extra", [
    ({"always_human": ["deploy/**", " ", 3]}, ("deploy/**",)),
    ({"always_human": "deploy/**"}, ()),              # malformed adds nothing
    ({}, ()),
])
def test_workflow_paths_add_to_never_replace_the_defaults(merge, extra):
    wf = WorkflowDef(path=Path("WORKFLOW.md"), config={"merge": merge}, prompt_template="")
    assert risktier.always_human(wf) == D + extra and risktier.always_human(None) == D
    tier = risktier.classify(["deploy/run.sh"], risktier.always_human(wf))[0]
    assert tier == ("human" if extra else "judge")
