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


CORE = ["chela/dispatcher.py", "chela/judge.py", "chela/judge_select.py", "chela/workflow.py",
        "chela/workflow_lint.py", "chela/sources/linear.py", "chela/sources/x/y.py",
        "chela/context.py",
        # CMX-296: the rest of the merge/containment machinery, and agent instructions
        "chela/automerge.py", "chela/overrides.py", "chela/gateanswer.py", "chela/reviewgate.py",
        "chela/sizegate.py", "chela/basesync.py", "chela/sweep.py", "chela/sweep_github.py",
        "chela/toolconfig.py", "chela/sandbox.py", "chela/sandbox_status.py",
        "chela/procguard.py", "CLAUDE.md", "AGENTS.md", "web/CLAUDE.md", "docs/sub/AGENTS.md"]
STANDARD = ["app.py", "chela/dashboard/app.py", "web/src/App.tsx", "chela/judgement.txt",
            "chela/contexts.py", "chela/sources.py", "chela/dispatcher.pyi", "xchela/judge.py",
            "docs/helper.py", "LICENSE", "chela/automerge.pyi", "chela/sweeper.txt",
            "CLAUDE.md.py", "web/CLAUDE.mdx"]


# ⚖️🪜 CMX-288 — every category, and each membership rule's boundary.
@pytest.mark.parametrize("files, tier", [
    (PROSE, "prose"),
    (["docs/x.md", "app.py"], "standard"),
    *[([p], "standard") for p in STANDARD],
    (STANDARD + PROSE, "standard"),
    *[([p], "core") for p in CORE],
    *[(["web/src/App.tsx", "docs/x.md", p], "core") for p in CORE],
    (None, "core"), ([], "core"),
])
def test_classify(files, tier):
    assert risktier.classify(files, D) == (tier, None)


@pytest.mark.parametrize("files", [["chela/dispatcher.py", "WORKFLOW.md"],
                                   ["app.py", "chela/judge.py", "plugin/x.json"]])
def test_always_human_wins_over_core_and_standard(files):
    assert risktier.classify(files, D)[0] == "human"


@pytest.mark.parametrize("merge, extra", [
    ({"always_human": ["deploy/**", " ", 3]}, ("deploy/**",)),
    ({"always_human": "deploy/**"}, ()),              # malformed adds nothing
    ({}, ()),
])
def test_workflow_paths_add_to_never_replace_the_defaults(merge, extra):
    wf = WorkflowDef(path=Path("WORKFLOW.md"), config={"merge": merge}, prompt_template="")
    assert risktier.always_human(wf) == D + extra and risktier.always_human(None) == D
    tier = risktier.classify(["deploy/run.sh"], risktier.always_human(wf))[0]
    assert tier == ("human" if extra else "standard")


@pytest.mark.parametrize("merge, extra", [
    ({"core": ["web/api/**", " ", 3]}, ("web/api/**",)),
    ({"core": "web/api/**"}, ()),                     # malformed adds nothing
    ({}, ()),
])
def test_workflow_core_paths_add_to_never_replace_the_defaults(merge, extra):
    wf = WorkflowDef(path=Path("WORKFLOW.md"), config={"merge": merge}, prompt_template="")
    defaults = ("chela/dispatcher.py", "chela/judge*.py", "chela/workflow*.py",
                "chela/sources/**", "chela/context.py", "chela/automerge.py",
                "chela/overrides.py", "chela/gateanswer.py", "chela/reviewgate.py",
                "chela/sizegate.py", "chela/basesync.py", "chela/sweep*.py",
                "chela/toolconfig.py", "chela/sandbox*.py", "chela/procguard.py")
    assert risktier.core(wf) == defaults + extra and risktier.core(None) == defaults
    core = risktier.core(wf)
    assert risktier.classify(["web/api/a.ts"], D, core)[0] == ("core" if extra else "standard")
    assert risktier.classify(["chela/context.py"], D, core)[0] == "core"   # never removed
