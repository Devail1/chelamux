"""⚡⚖️ CMX-489 — per-mutation selection for SHELL scripts.

A ``.sh`` has no import graph: a test or a module RUNS it by naming its path. Its observers
are the test files that name it, plus the observers of every Python module that names it,
plus — the same way — the observers of every shell script that names it. It used to run the
full suite for every experiment. A file under ``.github/workflows/`` stays full-suite.

Every repo here is a throwaway temp dir with a real pytest suite and real shell scripts.
"""
from __future__ import annotations

import pytest

from chela import judge
from chela import judge_select as js
from tests.test_judge_select import TEST_CMD, _cases, _exp, _repo, _why, calls  # noqa: F401

FILES = {
    "pkg/__init__.py": "",
    # Names scripts/tool.sh by path; the test that observes it imports THIS, never the script.
    "pkg/launcher.py": (
        "import subprocess\n\nSCRIPT = 'scripts/tool.sh'\n\n\ndef run():\n"
        "    return subprocess.run(['bash', SCRIPT], capture_output=True,\n"
        "                          text=True).stdout.strip()\n"
    ),
    "pkg/quiet.py": "X = 1\n",
    "scripts/env.sh": "GREETING=hello\n",
    # Sources env.sh: a test of run.sh observes env.sh only through this script.
    "scripts/run.sh": '. "$(dirname "$0")/env.sh"\necho "$GREETING"\n',
    "scripts/tool.sh": "echo tool\n",
    "scripts/orphan.sh": "echo nobody runs me\n",
    # Two scripts that name each other: following script→script must terminate.
    "scripts/ping.sh": "bash scripts/pong.sh\n",
    "scripts/pong.sh": "bash scripts/ping.sh\n",
    ".github/workflows/check.sh": "echo ci\n",
    ".github/scripts/lint.sh": "echo lint\n",
    "test_run.py": (
        "import subprocess\n\n\ndef test_run():\n"
        "    out = subprocess.run(['bash', 'scripts/run.sh'], capture_output=True, text=True)\n"
        "    assert out.stdout.strip() == 'hello'\n"
    ),
    "test_launcher.py": (
        "from pkg import launcher\n\n\ndef test_tool():\n    assert launcher.run() == 'tool'\n"
    ),
    "test_quiet.py": "from pkg import quiet\n\n\ndef test_quiet():\n    assert quiet.X == 1\n",
    "test_ping.py": "def test_ping():\n    assert 'scripts/ping.sh'\n",
    "test_ci.py": (
        "def test_ci():\n    assert '.github/workflows/check.sh' and '.github/scripts/lint.sh'\n"
    ),
    "test_unrelated.py": "def test_nothing():\n    assert True\n",
}


@pytest.fixture
def selector(tmp_path):
    root = _repo(tmp_path / "repo", FILES)
    return js.Selector(root, _cases(root))


def test_a_test_that_names_the_script_observes_it(selector):
    sel = selector.select("scripts/run.sh")
    assert sel.nodes == ["test_run.py"] and sel.expected == 1
    assert sel.why == "1 node(s) observe scripts/run.sh"


def test_the_observers_of_a_module_that_names_the_script_observe_it(selector):
    """GUARD: ``test_launcher.py`` never names ``tool.sh``; only ``pkg/launcher.py`` does, so
    only the module rule reaches it. ``test_quiet.py`` imports a sibling module, not it."""
    sel = selector.select("scripts/tool.sh")
    assert sel.nodes == ["test_launcher.py"], sel.why


def test_a_script_named_by_another_script_inherits_that_scripts_observers(selector):
    sel = selector.select("scripts/env.sh")
    assert sel.nodes == ["test_run.py"], sel.why


def test_scripts_that_name_each_other_terminate(selector):
    assert selector.select("scripts/pong.sh").nodes == ["test_ping.py"]
    assert selector.select("scripts/ping.sh").nodes == ["test_ping.py"]


def test_a_script_nothing_observes_runs_the_full_suite_and_is_not_untested(selector):
    sel = selector.select("scripts/orphan.sh")
    assert sel.full and sel.nodes == [] and sel.untested is False
    assert sel.why == "no test in the baseline observes it — full suite"


def test_a_ci_workflow_file_runs_the_full_suite_even_when_a_test_names_it(selector):
    sel = selector.select(".github/workflows/check.sh")
    assert sel.full and sel.nodes == []
    assert sel.why == "a CI workflow file is tool config no test observes — full suite"
    assert selector.select(".github/workflows/ci.yml").full


@pytest.mark.parametrize("rel", [".github/scripts/lint.sh", "docs/.github/workflows/x.sh"])
def test_only_the_workflows_directory_itself_is_forced_full(tmp_path, rel):
    files = {**FILES, "docs/.github/workflows/x.sh": "echo x\n",
             "test_ci.py": f"def test_ci():\n    assert '{rel}'\n"}
    root = _repo(tmp_path / "repo", files)
    assert js.Selector(root, _cases(root)).select(rel).nodes == ["test_ci.py"]


def test_a_selection_that_raises_runs_the_full_suite(selector, monkeypatch):
    def boom(self):
        raise RuntimeError("walk failed")

    monkeypatch.setattr(js.Selector, "_naming_sources", boom)
    sel = selector.select("scripts/tool.sh")
    assert sel.full and sel.why == "selection failed (walk failed) — full suite"


def test_naming_sources_are_this_repos_modules_and_scripts_only(tmp_path):
    """Test files the baseline ran are what selection SELECTS, never a naming source;
    a helper under ``tests/`` the baseline did not run as a test still is one."""
    files = {**FILES, ".venv/lib/x.py": "", ".git2/y.sh": "", "node_modules/y.py": "",
             "pkg/__pycache__/z.py": "", "web/venv/v.py": "", "lib/site-packages/s.py": "",
             "tests/helper.py": "", "README.md": ""}
    root = _repo(tmp_path / "repo", files)
    assert js.Selector(root, _cases(root))._naming_sources() == [
        ".github/scripts/lint.sh", ".github/workflows/check.sh", "pkg/__init__.py",
        "pkg/launcher.py", "pkg/quiet.py", "scripts/env.sh", "scripts/orphan.sh",
        "scripts/ping.sh", "scripts/pong.sh", "scripts/run.sh", "scripts/tool.sh",
        "tests/helper.py"]


def test_a_module_under_github_that_names_a_script_is_followed(tmp_path):
    files = {**FILES, ".github/tools/__init__.py": "",
             ".github/tools/ci.py": "S = 'scripts/orphan.sh'\n",
             "test_tools.py": "def test_t():\n    assert '.github/tools/ci.py'\n"}
    root = _repo(tmp_path / "repo", files)
    assert js.Selector(root, _cases(root)).select("scripts/orphan.sh").nodes == [
        "test_tools.py"]


# --- end to end: the judge runs the subset, and its verdict semantics are unchanged --------


def test_a_shell_mutation_the_subset_kills_runs_only_the_subset(tmp_path, calls):  # noqa: F811
    root = _repo(tmp_path / "repo", FILES)
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_exp("scripts/env.sh", "hello", "bye")]}, timeout=120)
    [o] = report.outcomes
    assert o.verdict == judge.KILLED and not o.confirmed_full, _why(o)
    assert o.selected == 1
    assert calls[1:] == [js.extend(TEST_CMD, ["test_run.py"])]


def test_a_shell_subset_survivor_is_still_confirmed_on_the_full_suite(tmp_path, calls):  # noqa: F811
    root = _repo(tmp_path / "repo", FILES)
    report = judge.run_experiments(
        root, TEST_CMD,
        {"experiments": [_exp("scripts/tool.sh", "echo tool", "echo tool  # x")]}, timeout=120)
    [o] = report.outcomes
    assert o.verdict == judge.SURVIVED and o.confirmed_full, _why(o)
    assert calls[1:] == [js.extend(TEST_CMD, ["test_launcher.py"]), TEST_CMD]
