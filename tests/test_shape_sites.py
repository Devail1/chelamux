"""⚖️🧩 CMX-335: a blocking verdict lists EVERY same-shape site of a survivor in the PR's diff.

CMX-274 took four rounds on one shape (`_end_agent` dropped at a call site): the judge named
three sites, the agent fixed three, the next round found more. The verdict must list them all,
found from the diff mechanically — so the acceptance fixture is 8 call sites and 1 survivor.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from chela import judge, shape_sites
from tests.test_judge import TEST_CMD, _git

# The PR's production module: 8 `_end_agent` call sites (one through `self.`), plus decoys
# that must NOT be listed — the `def` header and a different name sharing the prefix.
STOPS = [f"stop_{c}" for c in "abcdefg"]
REAPER_PY = (
    "def _end_agent(w):\n"
    "    return w\n\n\n"
    "def _end_agents_all():\n"
    "    return []\n\n\n"
    + "".join(f"def {s}(w_{s}):\n    _end_agent(w_{s})\n    return 1\n\n\n" for s in STOPS)
    + "class Pane:\n"
    "    def _end_agent(self, w):\n"
    "        return w\n\n"
    "    def close(self, w_self):\n"
    "        self._end_agent(w_self)\n"
    "        return _end_agents_all()\n"
)
# Proves none of the `_end_agent` calls happen, so dropping any of them SURVIVES.
WEAK_TEST = (
    "import reaper\n\n\n"
    + "".join(f"def test_{s}():\n    assert reaper.{s}(0) == 1\n\n\n" for s in STOPS)
    + "def test_close():\n    assert reaper.Pane().close(0) == []\n"
)


def _pr(root: Path) -> Path:
    """A repo whose ``origin/dev`` predates ``reaper.py``: every reaper line is in the diff."""
    root.mkdir(parents=True)
    (root / "README.md").write_text("base\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "update-ref", "refs/remotes/origin/dev", "HEAD")
    (root / "reaper.py").write_text(REAPER_PY)
    (root / "test_reaper.py").write_text(WEAK_TEST)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "the PR")
    return root


def _site_lines() -> list[str]:
    """The 8 expected ``reaper.py:N`` sites, computed from the fixture text by hand."""
    return [f"reaper.py:{n}" for n, ln in enumerate(REAPER_PY.splitlines(), 1)
            if ln.strip() in {f"_end_agent(w_{s})" for s in STOPS} | {"self._end_agent(w_self)"}]


def test_one_survivor_with_8_same_shape_call_sites_lists_ALL_8_in_the_block_body(tmp_path):
    root = _pr(tmp_path / "repo")
    exp = {"guard": "stop_c ends its agent", "file": "reaper.py",
           "before": "    _end_agent(w_stop_c)", "after": "    pass"}
    report = judge.run_experiments(root, TEST_CMD, {"experiments": [exp]}, timeout=120,
                                   base_branch="dev")
    assert [o.verdict for o in report.outcomes] == [judge.SURVIVED]
    sites = _site_lines()
    assert len(sites) == 8
    assert report.shape_groups == [{"kind": "call", "target": "_end_agent",
                                    "guards": ["stop_c ends its agent"], "sites": sites}]

    body = judge.block_body(report, None, TEST_CMD)
    assert "**`call` on `_end_agent`** — survived: stop_c ends its agent — 8 site(s)" in body
    for s in sites:
        assert f"- `{s}`" in body
    assert "corrupt EACH one by hand" in body
    # The decoys: the `def` headers and `_end_agents_all()` are never a site.
    assert body.count("- `reaper.py:") == 8


def test_a_held_out_survivor_is_never_grouped_or_named(tmp_path):
    root = _pr(tmp_path / "repo")
    exp = {"guard": "SECRET-HELD", "file": "reaper.py", "held_out": True,
           "before": "    _end_agent(w_stop_c)", "after": "    pass"}
    report = judge.run_experiments(root, TEST_CMD, {"experiments": [exp]}, timeout=120,
                                   base_branch="dev")
    assert report.state == judge.J_BLOCKED
    assert report.shape_groups == []
    assert "SECRET-HELD" not in judge.block_body(report, None, TEST_CMD)


def _outcome(guard: str, before: str, after: str):
    return SimpleNamespace(experiment=SimpleNamespace(guard=guard, before=before, after=after))


def test_survivors_of_one_shape_share_a_group_and_another_shape_gets_its_own(tmp_path):
    root = _pr(tmp_path / "repo")
    found = shape_sites.groups(root, "dev", [
        _outcome("g1", "    _end_agent(w_stop_a)", "    pass"),
        _outcome("g2", "        self._end_agent(w_self)", "        self._other(w_self)"),
        _outcome("g3", "    return 1", "    return 0"),
    ])
    assert [(g["kind"], g["target"], g["guards"]) for g in found] == [
        ("call", "_end_agent", ["g1", "g2"]), ("line", "return 1", ["g3"])]
    assert found[0]["sites"] == _site_lines()
    assert len(found[1]["sites"]) == 7


def test_an_unreadable_diff_is_None_never_no_sites(tmp_path, monkeypatch):
    root = _pr(tmp_path / "repo")
    o = [_outcome("g", "    _end_agent(w_stop_a)", "    pass")]
    assert shape_sites.groups(root, "nope", o)[0]["sites"] is None
    assert shape_sites.diff_sites(root, "", "call", "_end_agent") is None
    assert shape_sites.diff_sites(root, "dev", "call", "") is None
    # A changed file that cannot be read (gone: OSError; not text: UnicodeDecodeError).
    (root / "bin.py").write_bytes(b"\xff\xfe_end_agent(x)\n")
    for rel in ("gone.py", "bin.py"):
        monkeypatch.setattr(shape_sites.sweep, "changed_lines",
                            lambda wt, base, rel=rel: ({"reaper.py": {12}, rel: {1}}, ""))
        assert shape_sites.diff_sites(root, "dev", "call", "_end_agent") is None
    text = "\n".join(shape_sites.render([{"kind": "call", "target": "x", "guards": ["g"],
                                          "sites": None}]))
    assert "could not be listed (the diff was unreadable)" in text


def test_shape_kinds():
    assert shape_sites.shape("    _end_agent(w)", "    pass") == ("call", "_end_agent")
    assert shape_sites.shape("    a.b._end_agent(w)", "    a.b._kill(w)") == ("call", "_end_agent")
    # No call dropped, the comparison flipped ⇒ compare, spaces dropped.
    assert shape_sites.shape("if n == 3:", "if n != 3:") == ("compare", "n==3")
    assert shape_sites.shape("return 1", "return 0") == ("line", "return 1")
    assert shape_sites.shape("", "x") == ("line", "")


def test_matches():
    assert shape_sites.matches("call", "_end_agent", "  self._end_agent(w)")
    assert not shape_sites.matches("call", "_end_agent", "  _end_agents_all()")
    assert not shape_sites.matches("call", "_end_agent", "def _end_agent(w):")
    assert shape_sites.matches("compare", "n==3", "  if n  ==  3:")
    assert not shape_sites.matches("compare", "n==3", "  if n != 3:")
    assert shape_sites.matches("line", "return 1", "    return 1")
    assert not shape_sites.matches("line", "return 1", "    return 10")


def test_render_caps_the_list_and_counts_the_rest():
    sites = [f"f.py:{n}" for n in range(1, 54)]
    text = "\n".join(shape_sites.render([{"kind": "line", "target": "x", "guards": ["g"],
                                          "sites": sites}]))
    assert "- `f.py:50`" in text and "- `f.py:51`" not in text
    assert "- … and 3 more" in text
    assert shape_sites.render([]) == []


def test_a_held_out_survivors_line_is_never_listed_beside_a_visible_one(tmp_path):
    root = _pr(tmp_path / "repo")
    visible = {"guard": "stop_a ends its agent", "file": "reaper.py",
               "before": "    _end_agent(w_stop_a)", "after": "    pass"}
    held = {"guard": "SECRET-HELD", "file": "reaper.py", "held_out": True,
            "before": "    _end_agent(w_stop_c)\n    return 1", "after": "    return 1"}
    report = judge.run_experiments(root, TEST_CMD, {"experiments": [visible, held]},
                                   timeout=120, base_branch="dev")
    assert [o.verdict for o in report.outcomes] == [judge.SURVIVED, judge.SURVIVED]
    held_line = next(s for s in _site_lines()
                     if REAPER_PY.splitlines()[int(s.split(":")[1]) - 1].strip()
                     == "_end_agent(w_stop_c)")
    assert report.shape_groups[0]["sites"] == [s for s in _site_lines() if s != held_line]
    assert f"`{held_line}`" not in judge.block_body(report, None, TEST_CMD)


def test_a_site_finder_crash_never_costs_the_block(tmp_path, monkeypatch):
    root = _pr(tmp_path / "repo")

    def boom(*a, **k):
        raise RuntimeError("regex blew up")

    monkeypatch.setattr(shape_sites, "groups", boom)
    exp = {"guard": "g", "file": "reaper.py", "before": "    _end_agent(w_stop_c)",
           "after": "    pass"}
    report = judge.run_experiments(root, TEST_CMD, {"experiments": [exp]}, timeout=120,
                                   base_branch="dev")
    assert report.state == judge.J_BLOCKED and report.shape_groups == []
    assert "### 1. [MUTATION] g" in judge.block_body(report, None, TEST_CMD)


def test_a_compare_target_matches_whole_operands_only():
    assert shape_sites.matches("compare", "n<0", "if n < 0:")
    assert shape_sites.matches("compare", "n<0", "if (n<0)")
    assert not shape_sites.matches("compare", "n<0", "if min<0:")
    assert not shape_sites.matches("compare", "n<0", "if n<0x10:")
    assert not shape_sites.matches("compare", "n<0", "if self.n<0:")


def test_shape_of_a_multi_line_or_duplicate_call_corruption():
    # The target is the line the corruption CHANGED, never the multi-line before's first line.
    assert shape_sites.shape("x = 1\nreturn 1", "x = 1\nreturn 0") == ("line", "return 1")
    assert shape_sites.shape("x = 1\nif n == 3:", "x = 1\nif n != 3:") == ("compare", "n==3")
    # Only a whitespace change: no line differs stripped, so the first line stands.
    assert shape_sites.shape("    return 1", "return 1") == ("line", "return 1")
    # One of two same-name calls dropped is still a dropped call; both kept is not.
    assert shape_sites.shape("f(a)\nf(b)", "f(a)") == ("call", "f")
    assert shape_sites.shape("f(a)\nf(b)", "f(b)\nf(a)") == ("line", "f(a)")


def test_a_held_out_before_hides_EVERY_line_it_spans(tmp_path):
    root = _pr(tmp_path / "repo")
    lines = REAPER_PY.splitlines()
    at = lines.index("def stop_c(w_stop_c):")
    held = _outcome("SECRET", "\n".join(lines[at - 3:at + 2]), "")  # its call is the LAST line
    found = shape_sites.groups(root, "dev", [_outcome("g", "    _end_agent(w_stop_a)", "    pass")],
                               hidden=[SimpleNamespace(experiment=SimpleNamespace(
                                   file="reaper.py", before=held.experiment.before))])
    assert found[0]["sites"] == [s for s in _site_lines() if s != f"reaper.py:{at + 2}"]
