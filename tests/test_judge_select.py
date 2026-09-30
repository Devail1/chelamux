"""⚡⚖️ CMX-407 — per-mutation test selection, and the confirmation that keeps it honest.

A judge mutation used to re-run the whole suite. It now runs only the tests that can
observe the mutated file (:mod:`chela.judge_select`), and the verdict semantics must be
EXACTLY what they were:

* a red subset is final (KILLED is never re-run);
* a green subset is NOT — every subset survivor is re-run on the FULL suite, and only a
  full-suite-green survivor may block;
* anything selection cannot reason about runs the full suite. A mutation is never untested.

Every repo here is a throwaway temp dir with a real pytest suite; nothing touches the live
daemon.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from chela import judge
from chela import judge_select as js

TEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider'

# pkg/widget.py is imported DIRECTLY by test_direct.py (a weak test), and only INDIRECTLY —
# through pkg/other.py, never by name — by test_indirect.py (the strong one). That split is
# the whole point: the static import graph selects test_direct.py alone, which cannot see a
# broken `cue`, so a subset-green result MUST be confirmed on the full suite.
FILES = {
    "pkg/__init__.py": "",
    "pkg/widget.py": "def cue(x):\n    return x * 2\n\n\ndef unused():\n    return 1\n",
    "pkg/other.py": "from pkg.widget import cue as _c\n\n\ndef double(x):\n    return _c(x)\n",
    "pkg/gauge.py": "def level():\n    return 5\n",
    "test_direct.py": (
        "from pkg import widget\n\n\ndef test_weak():\n    assert callable(widget.cue)\n"
    ),
    "test_indirect.py": (
        "from pkg.other import double\n\n\ndef test_strong():\n    assert double(3) == 6\n"
    ),
    "test_gauge.py": (
        "from pkg import gauge\n\n\ndef test_level():\n    assert gauge.level() == 5\n"
    ),
    "test_unrelated.py": "def test_nothing():\n    assert True\n",
    "style.css": ".chip { color: red; }\n",
    "test_css.py": (
        "from pathlib import Path\n\n\ndef test_css():\n"
        "    assert 'color' in Path('style.css').read_text()\n"
    ),
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(root: Path, files: dict[str, str] = FILES) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return root


def _cases(root: Path) -> list[js.Case]:
    """The universe exactly as a real baseline reports it — a real pytest, a real JUnit."""
    junit = root.parent / "junit.xml"
    subprocess.run(f'{TEST_CMD} --junitxml="{junit}"', shell=True, cwd=root, check=True,
                   capture_output=True)
    cases = js.parse_junit(root, junit)
    assert cases, "the JUnit report parsed to nothing"
    return cases


def _exp(file: str, before: str, after: str, guard: str = "g") -> dict:
    return {"guard": guard, "file": file, "before": before, "after": after}


@pytest.fixture
def calls(monkeypatch):
    """Every command the judge ran, in order — to prove WHICH suite decided a verdict."""
    seen: list[str] = []
    real = judge.run_suite

    def spy(test_cmd, cwd, timeout=judge.SUITE_TIMEOUT_SECONDS, extra_env=None):
        seen.append(test_cmd)
        return real(test_cmd, cwd, timeout, extra_env)

    monkeypatch.setattr(judge, "run_suite", spy)
    return seen


# --- selection -----------------------------------------------------------------------------


def test_selection_includes_the_importers_of_the_mutated_module(tmp_path):
    """GUARD: the static import graph. ``from pkg import widget`` names neither
    ``widget.py`` nor ``pkg.widget`` as text, so only the AST import walk can find it."""
    root = _repo(tmp_path / "repo")
    sel = js.Selector(root, _cases(root)).select("pkg/widget.py")

    assert not sel.full, sel.why
    assert "test_direct.py" in sel.nodes
    assert "test_unrelated.py" not in sel.nodes and "test_gauge.py" not in sel.nodes
    assert sel.expected == 1


def test_an_unknown_file_type_falls_back_to_the_full_suite(tmp_path):
    """GUARD: ``style.css`` IS named by a test (``test_css.py`` reads it off disk), so a
    selector that forgot the file-type rule would happily narrow it. It must not."""
    root = _repo(tmp_path / "repo")
    sel = js.Selector(root, _cases(root)).select("style.css")

    assert sel.full and sel.nodes == []
    assert "not a file type" in sel.why


def test_a_conftest_and_an_unobserved_file_run_the_full_suite(tmp_path):
    root = _repo(tmp_path / "repo", {**FILES, "conftest.py": "", "pkg/orphan.py": "X = 1\n"})
    selector = js.Selector(root, _cases(root))

    assert selector.select("conftest.py").full
    assert selector.select("pkg/orphan.py").full        # nothing observes it ⇒ never skipped


def test_selection_never_reaches_outside_what_the_baseline_ran(tmp_path):
    """A test the baseline did not run cannot vouch for a KILL — a red there is red on the
    unmutated tree too. The universe is the JUnit report, not the filesystem."""
    root = _repo(tmp_path / "repo")
    cases = [c for c in _cases(root) if c.file != "test_direct.py"]
    sel = js.Selector(root, cases).select("pkg/widget.py")

    assert "test_direct.py" not in sel.nodes


def test_a_test_that_names_the_module_by_dotted_path_is_selected(tmp_path):
    files = {**FILES, "test_patch.py": (
        "def test_patch(monkeypatch):\n"
        "    monkeypatch.setattr('pkg.gauge.level', lambda: 5)\n"
    )}
    root = _repo(tmp_path / "repo", files)
    sel = js.Selector(root, _cases(root)).select("pkg/gauge.py")

    assert {"test_gauge.py", "test_patch.py"} <= set(sel.nodes)


def test_js_selection_follows_the_import_graph_transitively(tmp_path):
    """GUARD: a JS suite imports ``app.js``, which imports the mutated ``util.js``. Direct
    importers alone would select nothing and fall back to the full suite every time."""
    root = tmp_path / "repo"
    for rel, text in {
        "web/util.js": "export const x = 1;\n",
        "web/app.js": "import { x } from './util.js';\nexport const y = x;\n",
        "tests/app.test.mjs": "import { y } from '../web/app.js';\n",
        "tests/other.test.mjs": "import assert from 'node:assert';\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    cases = [
        js.Case("test_js.py", "test_js.py::test_js[tests/app.test.mjs]", True,
                "tests/app.test.mjs"),
        js.Case("test_js.py", "test_js.py::test_js[tests/other.test.mjs]", True,
                "tests/other.test.mjs"),
    ]
    sel = js.Selector(root, cases).select("web/util.js")

    assert sel.nodes == ["test_js.py::test_js[tests/app.test.mjs]"]
    assert sel.expected == 1


def test_the_coverage_map_adds_tests_the_static_graph_cannot_see(tmp_path):
    """GUARD: ``test_indirect.py`` reaches ``pkg/widget.py`` only through ``pkg/other.py``.
    Coverage recorded it touching the file; selection must use that."""
    root = _repo(tmp_path / "repo")
    db = tmp_path / ".coverage"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT);"
        "CREATE TABLE context (id INTEGER PRIMARY KEY, context TEXT);"
        "CREATE TABLE line_bits (file_id INTEGER, context_id INTEGER, numbits BLOB);"
    )
    conn.execute("INSERT INTO file VALUES (1, ?)", (str(root / "pkg" / "widget.py"),))
    conn.execute("INSERT INTO context VALUES (1, 'test_indirect.py::test_strong|run')")
    conn.execute("INSERT INTO context VALUES (2, '')")
    conn.execute("INSERT INTO line_bits VALUES (1, 1, x'01')")
    conn.execute("INSERT INTO line_bits VALUES (1, 2, x'01')")
    conn.commit()
    conn.close()

    cov = js.parse_coverage(root, db)
    assert cov == {"pkg/widget.py": ["test_indirect.py"]}
    sel = js.Selector(root, _cases(root), cov).select("pkg/widget.py")
    assert {"test_direct.py", "test_indirect.py"} <= set(sel.nodes)


def test_only_a_single_pytest_invocation_is_extendable():
    """GUARD: appending node ids to a chained command would hand them to whatever runs
    LAST — ``npm test`` — while pytest ran everything. Those stay on the full suite."""
    assert js.extendable("CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q")
    assert js.extendable('"/usr/bin/python3" -m pytest -q')
    assert not js.extendable("pytest -q && npm test")
    assert not js.extendable("pytest -q | tee log")
    assert not js.extendable("npm test")
    assert js.extend("pytest -q", ["a.py::t[x/y.mjs]"]) == "pytest -q 'a.py::t[x/y.mjs]'"


# --- the verdicts --------------------------------------------------------------------------


def test_a_subset_green_survivor_that_the_full_suite_kills_is_NOT_a_survivor(tmp_path, calls):
    """GUARD — the full-suite confirmation. Only ``test_direct.py`` (weak) imports
    ``pkg/widget.py``, so the subset stays green under a broken ``cue``; ``test_indirect.py``
    catches it. Reporting the subset's green as SURVIVED would BLOCK a good PR."""
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_exp("pkg/widget.py", "x * 2", "x * 3")]}, timeout=120,
    )

    assert not report.cannot_verify, report.cannot_verify
    [o] = report.outcomes
    assert o.verdict == judge.KILLED, o.reason
    assert o.confirmed_full and o.selected == 1
    assert report.blocking == []
    assert calls[-1] == TEST_CMD            # the verdict came from the FULL suite


def test_a_subset_green_AND_full_green_survivor_still_BLOCKS(tmp_path, calls):
    """⭐ The case that must be ACCEPTED: nothing tests ``unused()``. Subset green, full suite
    green ⇒ the guard is not a guard, and selection must not have softened that."""
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_exp("pkg/widget.py", "return 1", "return 2")]},
        timeout=120,
    )

    [o] = report.outcomes
    assert o.verdict == judge.SURVIVED, o.reason
    assert o.confirmed_full
    assert report.blocking == [o] and report.state == judge.J_BLOCKED
    assert o.mutated is not None and o.mutated.passed == 5     # the FULL suite's count


def test_a_KILLED_subset_is_final_and_is_not_re_run(tmp_path, calls):
    """GUARD: ``test_gauge.py`` imports ``pkg/gauge.py`` and catches the mutation. A red
    subset is a red full suite; the judge must not pay for a second run to learn it."""
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_exp("pkg/gauge.py", "return 5", "return 6")]},
        timeout=120,
    )

    [o] = report.outcomes
    assert o.verdict == judge.KILLED and not o.confirmed_full
    assert len(calls) == 2, calls           # the baseline + ONE subset run, nothing else
    assert calls[1] != TEST_CMD and "test_gauge.py" in calls[1]


def test_an_unknown_file_type_mutation_runs_the_full_suite(tmp_path, calls):
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_exp("style.css", "color", "colour")]}, timeout=120,
    )

    [o] = report.outcomes
    assert o.verdict == judge.KILLED and o.selected is None
    assert calls[1:] == [TEST_CMD]


def test_selection_off_runs_every_mutation_on_the_full_suite(tmp_path, calls):
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_exp("pkg/gauge.py", "return 5", "return 6")]},
        timeout=120, select_tests=False,
    )

    assert report.outcomes[0].verdict == judge.KILLED
    assert calls == [TEST_CMD, TEST_CMD]
    assert "off" in report.selection


def test_a_chained_test_cmd_keeps_the_full_suite(tmp_path, calls):
    root = _repo(tmp_path / "repo")
    cmd = f"{TEST_CMD} && true"
    report = judge.run_experiments(
        root, cmd, {"experiments": [_exp("pkg/gauge.py", "return 5", "return 6")]}, timeout=120,
    )

    assert report.outcomes[0].verdict == judge.KILLED
    assert calls == [cmd, cmd]


# --- what the verdict says -----------------------------------------------------------------


def test_the_verdict_header_shows_the_judge_duration():
    """GUARD: a slow judge must be visible on the PR itself, not only in the daemon log."""
    report = judge.Report(battery_seconds=125, total_seconds=611,
                          selection="per-mutation selection over the baseline's 9 test(s)")
    body = judge.comment_body(report, "https://example.invalid/pr/1", "pytest -q")
    header = body.split("\n\n", 2)[1]

    assert "judge took **10m 11s**" in header and "mutation battery 2m 05s" in header


def test_the_block_body_says_which_suite_decided_each_survivor(tmp_path):
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_exp("pkg/widget.py", "return 1", "return 2")]},
        timeout=120,
    )
    body = judge.block_body(report, None, TEST_CMD)

    assert "confirmed on the full suite" in body
    assert "⏱️" in body.split("\n\n", 2)[1]


# --- CMX-395 × CMX-407: held-out experiments and the consistency re-run --------------------


def _held(file: str, before: str, after: str, guard: str) -> dict:
    return dict(_exp(file, before, after, guard), held_out=True)


def test_a_held_out_subset_survivor_is_confirmed_on_the_full_suite_before_it_blocks(
    tmp_path, calls,
):
    """GUARD: a held-out experiment takes the SAME subset → full-suite path as a visible one.
    The broken ``cue`` is subset-green (only the weak test imports it) and full-suite red: it
    must NOT block, not even as an anonymous held-out count. The unguarded ``unused()`` is
    green on both and MUST block — as a count only, never by name."""
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [
            _held("pkg/widget.py", "x * 2", "x * 3", "SECRET-cue"),
            _held("pkg/widget.py", "return 1", "return 2", "SECRET-unused"),
        ]}, timeout=120,
    )

    assert not report.cannot_verify, report.cannot_verify
    cue, unused = report.outcomes
    assert cue.held_out and cue.confirmed_full and cue.subset_verdict == judge.SURVIVED
    assert cue.verdict == judge.KILLED, cue.reason          # the full suite killed it
    assert unused.verdict == judge.SURVIVED and unused.confirmed_full
    assert report.blocking == [unused] and report.held_out_blocking == [unused]
    assert report.visible_blocking == [] and report.state == judge.J_BLOCKED
    assert calls[1:] == [                                   # subset, FULL — for each one
        js.extend(TEST_CMD, ["test_direct.py"]), TEST_CMD,
        js.extend(TEST_CMD, ["test_direct.py"]), TEST_CMD,
    ]
    public = judge.block_body(report, None, TEST_CMD) + judge.comment_body(report, None, TEST_CMD)
    assert "SECRET" not in public and "1 held-out guard(s) also survived" in public


def test_the_consistency_re_run_replays_the_first_runs_own_selection(tmp_path, calls):
    """GUARD: a subset first run must be re-run on the SAME subset. Re-running it on the full
    suite would compare two different experiments and call the difference a flip."""
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_exp("pkg/gauge.py", "return 5", "return 6")]},
        timeout=120, consistency_sample=1,
    )

    [o] = report.outcomes
    assert o.verdict == judge.KILLED and o.rerun_verdict == judge.KILLED and not o.flaky
    subset = js.extend(TEST_CMD, ["test_gauge.py"])
    assert calls[1:] == [subset, subset], calls
    assert report.consistency["sampled"] == 1


def test_a_confirmed_survivor_is_re_run_through_subset_then_full(tmp_path, calls):
    """⭐ ACCEPTED: a subset-green AND full-green survivor is sampled first, re-run the same
    way, stays SURVIVED — and still BLOCKS."""
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_exp("pkg/widget.py", "return 1", "return 2")]},
        timeout=120, consistency_sample=1,
    )

    [o] = report.outcomes
    assert o.rerun_verdict == judge.SURVIVED and not o.flaky
    assert report.blocking == [o]
    subset = js.extend(TEST_CMD, ["test_direct.py"])
    assert calls[1:] == [subset, TEST_CMD, subset, TEST_CMD], calls


def test_a_survivor_the_full_suite_killed_is_never_sampled_for_consistency(tmp_path, calls):
    """GUARD — the order. subset SURVIVED → full-suite KILLED is not a survivor, and its two
    measurements disagreed by design (narrow selection), not by flake: it is never re-run and
    can never be marked flaky. Only the ordinary KILLED experiment is sampled."""
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [
            _exp("pkg/widget.py", "x * 2", "x * 3", "cue"),
            _exp("pkg/gauge.py", "return 5", "return 6", "gauge"),
        ]}, timeout=120, consistency_sample=5,
    )

    cue, gauge = report.outcomes
    assert cue.verdict == judge.KILLED and cue.subset_verdict == judge.SURVIVED
    assert cue.rerun_verdict == "" and not cue.flaky
    assert gauge.rerun_verdict == judge.KILLED
    assert report.consistency["sampled"] == 1


# --- the baseline's coverage path ----------------------------------------------------------
#
# pytest-cov is not a dependency here, so these stand coverage in with an env var: the
# "coverage" args are none, the "coverage" env is FAKE_COV=1, and `test_cov_sensitive.py`
# goes red whenever it is set — i.e. a suite that is red ONLY under coverage.

COV_ENV = {"FAKE_COV": "1"}
COV_SENSITIVE = {
    "test_cov_sensitive.py": (
        "import os\n\n\ndef test_plain_only():\n    assert 'FAKE_COV' not in os.environ\n"
    ),
}


@pytest.fixture
def fake_cov(monkeypatch, tmp_path):
    """pytest-cov "installed", its args swapped for COV_ENV, the per-sha cache in tmp, and
    every (cmd, env) run_baseline ran recorded along with every coverage-map read."""
    runs: list[tuple[str, dict]] = []
    parsed: list[Path] = []
    real = judge.run_suite

    def spy(test_cmd, cwd, timeout=judge.SUITE_TIMEOUT_SECONDS, extra_env=None):
        runs.append((test_cmd, dict(extra_env or {})))
        return real(test_cmd, cwd, timeout, extra_env)

    monkeypatch.setattr(judge, "run_suite", spy)
    monkeypatch.setattr(judge, "_has_pytest_cov", lambda wt: True)
    monkeypatch.setattr(js, "coverage_args", lambda data_file: ([], dict(COV_ENV)))
    monkeypatch.setattr(js, "_cache_dir", lambda: tmp_path / "cov-cache")
    state = {"map": {"pkg/widget.py": ["test_indirect.py"]}}

    def parse(root, data_file):
        parsed.append(data_file)
        return dict(state["map"])

    monkeypatch.setattr(js, "parse_coverage", parse)
    return {"runs": runs, "parsed": parsed, "state": state}


def test_a_baseline_red_only_under_coverage_is_re_run_plain_never_CANNOT_VERIFY(
    tmp_path, fake_cov,
):
    """GUARD: coverage is an optimisation. A suite green plain but red under coverage must be
    re-run WITHOUT coverage and judged on that — a CANNOT VERIFY here would stall every PR in
    a repo whose suite dislikes coverage. The plain re-run wrote no coverage data, so the map
    must not be read off it either."""
    root = _repo(tmp_path / "repo", {**FILES, **COV_SENSITIVE})
    baseline, selector, why = judge.run_baseline(root, TEST_CMD, 120, tmp_path)

    assert baseline.green, baseline.tail
    assert selector is not None, why
    assert [env for _, env in fake_cov["runs"]] == [COV_ENV, {}]
    assert fake_cov["parsed"] == []                  # nothing read off the plain re-run
    assert selector.coverage == {}
    assert f"over the baseline's {len(js.parse_junit(root, tmp_path / 'baseline-junit.xml'))}" \
        in why


def test_a_baseline_red_even_plain_is_red_and_selects_nothing(tmp_path, fake_cov):
    """Control for the re-run: a suite that is red WITHOUT coverage stays red — the re-run is
    for coverage-only reds, it must not launder a genuinely red baseline."""
    root = _repo(tmp_path / "repo", {**FILES, "test_red.py": "def test_red():\n    assert 0\n"})
    baseline, selector, _ = judge.run_baseline(root, TEST_CMD, 120, tmp_path)

    assert not baseline.green and selector is None
    assert [env for _, env in fake_cov["runs"]] == [COV_ENV, {}]


def test_a_red_baseline_WITHOUT_coverage_is_not_re_run(tmp_path, fake_cov, monkeypatch):
    """GUARD: the plain re-run is only for a run that HAD coverage on. A plain red is final —
    re-running it costs a full suite and can only flake it green."""
    monkeypatch.setattr(judge, "_has_pytest_cov", lambda wt: False)
    root = _repo(tmp_path / "repo", {**FILES, "test_red.py": "def test_red():\n    assert 0\n"})
    baseline, selector, _ = judge.run_baseline(root, TEST_CMD, 120, tmp_path)

    assert not baseline.green and selector is None
    assert [env for _, env in fake_cov["runs"]] == [{}]


def test_a_green_coverage_baseline_builds_the_map_and_caches_it_per_head_sha(
    tmp_path, fake_cov,
):
    """GUARD: the map is built ONCE per head sha. The first baseline runs under coverage,
    reads the map and caches it; a second baseline at the same sha runs plain and uses the
    cached map; a new sha builds its own."""
    root = _repo(tmp_path / "repo")
    sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()

    b1, s1, why1 = judge.run_baseline(root, TEST_CMD, 120, tmp_path)
    assert b1.green and s1 is not None
    assert s1.coverage == {"pkg/widget.py": ["test_indirect.py"]}
    assert fake_cov["parsed"] == [tmp_path / ".coverage"] and "coverage map: 1 file(s)" in why1
    assert js.load_cached_coverage(sha) == s1.coverage

    fake_cov["state"]["map"] = {"pkg/gauge.py": ["test_gauge.py"]}   # a re-parse would show
    b2, s2, why2 = judge.run_baseline(root, TEST_CMD, 120, tmp_path)
    assert b2.green and s2.coverage == {"pkg/widget.py": ["test_indirect.py"]}
    assert "coverage map: cached" in why2
    assert [env for _, env in fake_cov["runs"]] == [COV_ENV, {}]     # the 2nd ran plain
    assert len(fake_cov["parsed"]) == 1

    (root / "test_unrelated.py").write_text("def test_nothing():\n    assert 1\n")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "next")
    b3, s3, _ = judge.run_baseline(root, TEST_CMD, 120, tmp_path)
    assert s3.coverage == {"pkg/gauge.py": ["test_gauge.py"]}        # a new sha, a new map
    assert fake_cov["runs"][-1][1] == COV_ENV


def test_the_coverage_map_reaches_the_selection_of_a_mutation(tmp_path, fake_cov):
    """GUARD, end to end: the static graph selects only ``test_direct.py`` for
    ``pkg/widget.py``; the coverage map adds ``test_indirect.py``, which KILLS a broken
    ``cue`` on the subset — no full-suite confirmation needed."""
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_exp("pkg/widget.py", "x * 2", "x * 3")]}, timeout=120,
    )

    [o] = report.outcomes
    assert o.verdict == judge.KILLED and not o.confirmed_full, o.reason
    assert o.selected == 2
    assert "coverage map: 1 file(s)" in report.selection


def test_has_pytest_cov_asks_the_JUDGED_trees_own_interpreter(tmp_path):
    """GUARD: pytest-cov is detected in the judged tree's ``.venv``, not the judge's own
    interpreter — and with no ``.venv`` there is no coverage run at all."""
    root = tmp_path / "wt"
    assert judge._has_pytest_cov(root) is False
    libs = tmp_path / "libs"
    libs.mkdir()
    py = judge._venv_python(root)
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/sh\nPYTHONPATH="{libs}" exec "{sys.executable}" "$@"\n')
    py.chmod(0o755)
    assert judge._has_pytest_cov(root) is False
    (libs / "pytest_cov.py").write_text("")
    assert judge._has_pytest_cov(root) is True


# --- rework r3: the four survivors of round 2, and the invariants around them --------------


def test_a_subset_that_COLLAPSED_is_re_run_on_the_full_suite_never_final(tmp_path, calls):
    """GUARD: only a KILLED subset is final. A module-level ``raise`` takes ``test_gauge.py``
    down at collection — the subset is INVALID (it errored, it did not trip a guard), which
    proves nothing either way, so the FULL suite must decide it. Treating every non-SURVIVED
    subset as final would skip that run."""
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [
            _exp("pkg/gauge.py", "def level():", "raise RuntimeError('down')\n\n\ndef level():"),
        ]}, timeout=120,
    )

    [o] = report.outcomes
    assert o.subset_verdict == judge.INVALID, o.reason
    assert o.confirmed_full
    assert calls[1:] == [js.extend(TEST_CMD, ["test_gauge.py"]), TEST_CMD], calls
    assert "inconclusive → confirmed on the full suite" in o.measured_by


def test_every_measured_verdict_but_KILLED_is_confirmed_on_the_full_suite(tmp_path, calls):
    """The invariant itself, over all three subset outcomes in ONE battery: KILLED is the
    only verdict a subset may settle; SURVIVED and INVALID both cost a full run."""
    root = _repo(tmp_path / "repo")
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [
            _exp("pkg/gauge.py", "return 5", "return 6", "killed"),
            _exp("pkg/widget.py", "return 1", "return 2", "survived"),
            _exp("pkg/gauge.py", "def level():", "raise RuntimeError('x')\ndef level():",
                 "invalid"),
        ]}, timeout=120,
    )

    killed, survived, invalid = report.outcomes
    assert (killed.verdict, killed.confirmed_full) == (judge.KILLED, False)
    assert survived.confirmed_full and survived.subset_verdict == judge.SURVIVED
    assert invalid.confirmed_full and invalid.subset_verdict == judge.INVALID
    assert calls.count(TEST_CMD) == 2              # the two confirmations, nothing else


def test_a_dirty_worktree_never_caches_a_coverage_map_under_HEADs_sha(tmp_path, fake_cov):
    """GUARD: a self-check runs on a worktree with UNCOMMITTED edits. Its coverage belongs to
    that tree, not to HEAD — caching it under HEAD's sha would hand the next clean judge of
    that commit a map of code that was never committed. And with nothing cached, a second
    dirty baseline must build its map afresh (under coverage), not read one back."""
    root = _repo(tmp_path / "repo")
    sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert judge._head_sha(root) == sha                   # clean ⇒ the real sha
    (root / "test_unrelated.py").write_text("def test_nothing():\n    assert 1 == 1\n")
    assert judge._head_sha(root) == ""                    # dirty ⇒ no sha at all

    b1, s1, _ = judge.run_baseline(root, TEST_CMD, 120, tmp_path)
    b2, s2, why2 = judge.run_baseline(root, TEST_CMD, 120, tmp_path)

    assert b1.green and b2.green and s1 is not None and s2 is not None
    assert js.load_cached_coverage(sha) is None
    assert not (tmp_path / "cov-cache").exists() or not list((tmp_path / "cov-cache").iterdir())
    assert [env for _, env in fake_cov["runs"]] == [COV_ENV, COV_ENV]
    assert "cached" not in why2


def test_untracked_files_do_not_make_a_worktree_dirty_for_the_cache(tmp_path):
    root = _repo(tmp_path / "repo")
    (root / "scratch.txt").write_text("junk")
    assert judge._head_sha(root) != ""


@pytest.mark.parametrize("rel,src,want", [
    ("pkg/test_x.py", "from . import widget", {"pkg", "pkg.widget"}),
    ("pkg/test_x.py", "from .widget import cue", {"pkg.widget", "pkg.widget.cue"}),
    ("pkg/sub/test_x.py", "from ..widget import cue", {"pkg.widget", "pkg.widget.cue"}),
    ("pkg/sub/deep/test_x.py", "from ... import gauge", {"pkg", "pkg.gauge"}),
    ("pkg/sub/test_x.py", "from .. import widget", {"pkg", "pkg.widget"}),
])
def test_relative_imports_resolve_against_the_importers_package(rel, src, want):
    """GUARD: a relative import names its module RELATIVE to the importing file. Read as
    absolute, ``from .widget import cue`` becomes a top-level ``widget`` that no module is."""
    got = js.py_imports(rel, src)
    assert want <= got, got
    assert "widget" not in got and "widget.cue" not in got and "gauge" not in got


def test_absolute_and_function_local_imports_are_all_seen():
    got = js.py_imports("t.py", (
        "import a.b\nfrom c import d\n\n\ndef f():\n    import e.f\n    from g.h import i\n"
    ))
    assert {"a.b", "c", "c.d", "e.f", "g.h", "g.h.i"} <= got


def test_a_relative_importer_is_selected_end_to_end(tmp_path):
    """GUARD, end to end: ``pkg/tests/test_rel.py`` reaches ``pkg/gauge.py`` ONLY through
    ``from ..gauge import level`` — it never names ``gauge.py`` or ``pkg.gauge`` as text."""
    root = _repo(tmp_path / "repo", {
        **FILES,
        "pkg/tests/__init__.py": "",
        "pkg/tests/test_rel.py": (
            "from ..gauge import level\n\n\ndef test_rel():\n    assert level() == 5\n"
        ),
    })
    sel = js.Selector(root, _cases(root)).select("pkg/gauge.py")

    assert "pkg/tests/test_rel.py" in sel.nodes, sel.nodes


def test_module_names_cover_packages_and_tests_helpers():
    assert js.module_names("chela/judge.py") == {"chela.judge"}
    assert js.module_names("chela/__init__.py") == {"chela"}
    assert js.module_names("tests/helpers.py") == {"tests.helpers", "helpers"}


def test_a_skipped_or_failed_baseline_case_is_not_counted_as_expected(tmp_path):
    """The subset's collapse baseline counts only cases that PASSED in the baseline."""
    root = _repo(tmp_path / "repo", {**FILES, "test_gauge.py": (
        "import pytest\nfrom pkg import gauge\n\n\ndef test_level():\n"
        "    assert gauge.level() == 5\n\n\n@pytest.mark.skip\ndef test_skipped():\n"
        "    assert gauge.level() == 5\n"
    )})
    cases = _cases(root)
    assert [c.passed for c in cases if c.file == "test_gauge.py"] == [True, False]
    sel = js.Selector(root, cases).select("pkg/gauge.py")
    assert sel.nodes == ["test_gauge.py"] and sel.expected == 1


# --- timing: per experiment, per battery, per judge ----------------------------------------


def test_each_experiment_and_the_battery_record_their_own_time(tmp_path, caplog):
    """GUARD: the time per experiment and the battery's total are MEASURED — every one of them
    ran a real pytest, so none can be zero, and the battery (baseline included) takes at least
    as long as its experiments together."""
    root = _repo(tmp_path / "repo")
    with caplog.at_level("INFO", logger="chela.judge"):
        report = judge.run_experiments(
            root, TEST_CMD, {"experiments": [
                _exp("pkg/gauge.py", "return 5", "return 6", "gauge"),
                _exp("style.css", "color", "colour", "css"),
            ]}, timeout=120,
        )

    gauge, css = report.outcomes
    assert gauge.seconds > 0 and css.seconds > 0
    assert report.battery_seconds >= gauge.seconds + css.seconds > 0
    assert gauge.as_dict()["selected"] == 1 and css.as_dict()["selected"] is None
    assert gauge.as_dict()["seconds"] == round(gauge.seconds, 1)
    assert "1 selected test(s)" in caplog.text and "mutation battery took" in caplog.text


@pytest.mark.parametrize("selected,confirmed,subset,want", [
    (None, False, "", "full suite, 7s"),
    (3, False, "", "3 selected test(s), 7s"),
    (3, True, judge.SURVIVED, "3 selected test(s) green → confirmed on the full suite, 7s"),
    (3, True, judge.INVALID,
     "3 selected test(s) inconclusive → confirmed on the full suite, 7s"),
])
def test_measured_by_names_the_suite_that_decided_it(selected, confirmed, subset, want):
    o = judge.Outcome(judge.Experiment(guard="g", file="f.py", before="a", after="b"),
                      judge.KILLED, "r")
    o.selected, o.confirmed_full, o.subset_verdict, o.seconds = selected, confirmed, subset, 7
    assert o.measured_by == want


def test_the_header_omits_timing_that_was_never_measured():
    assert judge._timing_line(judge.Report()) == ""
    only_battery = judge._timing_line(judge.Report(battery_seconds=5))
    assert "judge took" not in only_battery and "mutation battery 5s" in only_battery
