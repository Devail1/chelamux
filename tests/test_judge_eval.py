"""📏⚖️ CMX-408: the offline judge-design eval — its scorer, grader, split and cost gate.

No test here makes a model call: every runner is a fake. The integration tests build a tiny
real git repo so the eval's own git plumbing (archive, diff, show) runs for real.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from chela import judge
from chela.judge_eval import dataset as ds
from chela.judge_eval import design, evaluate, grade, mine, report
from chela.judge_eval.runner import ClaudeCLIRunner, RunResult

# --- fixtures ----------------------------------------------------------------------

PROD = '''\
RECHECK = 2.0


def allowed(verdict, checked_at, now):
    if checked_at is None or now - checked_at >= RECHECK:
        verdict = probe()
    return verdict == "ok"


def label(name):
    return name.strip()
'''

SEED = ds.Target(id="seed-x", file="app/core.py",
                 before="if checked_at is None or now - checked_at >= RECHECK:",
                 after="if checked_at is None:", source=ds.SEEDED, label=ds.REAL)
DECOY = ds.Target(id="decoy-x", file="app/other.py", before="LIMIT = 10",
                  after="LIMIT = 1", source=ds.DECOY)

HIT = {"guard": "recheck", "file": "app/core.py", "kind": "mutation",
       "before": "    if checked_at is None or now - checked_at >= RECHECK:",
       "after": "    if checked_at is None:"}
# Same file, the adjacent line — a plan that circles the weakness but never changes it.
NEAR_MISS = {"guard": "probe", "file": "app/core.py", "kind": "mutation",
             "before": "        verdict = probe()", "after": "        verdict = None"}
OTHER_FILE_SAME_TEXT = {**HIT, "file": "app/copy.py"}


def _pairs():
    return {"app/core.py": PROD, "app/copy.py": PROD}


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                        "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo)})


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "app").mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    (r / "app" / "other.py").write_text("LIMIT = 10\n")
    (r / "app" / "core.py").write_text("RECHECK = 2.0\n")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "base")
    (r / "app" / "core.py").write_text(PROD)
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "feat: re-check the verdict")
    head = subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    base = subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD^"], capture_output=True,
                          text=True).stdout.strip()
    return r, base, head


def _pr_on(split: str, start: int = 1000) -> int:
    n = start
    while ds.split_for(n) != split:
        n += 1
    return n


def _case(base, head, pr, cid=None, targets=None, kind=ds.SEEDED):
    return ds.Case(id=cid or f"case-{pr}", pr=pr, kind=kind, title=f"PR {pr}", base_sha=base,
                   head_sha=head, split=ds.split_for(pr),
                   targets=list(targets if targets is not None else [SEED, DECOY]))


class FakeDesign:
    """A design-step runner that returns a fixed plan, recording when it was called."""

    def __init__(self, experiments, log=None):
        self.experiments = experiments
        self.calls = []
        self.log = log

    def run(self, prompt, *, cwd, schema, tools=("Read", "Grep", "Glob")):
        self.calls.append({"prompt": prompt, "cwd": cwd, "log_len": len(self.log or [])})
        return RunResult({"experiments": list(self.experiments), "notes": []}, 0.5, "")


class FakeGrader:
    """Grades every pending experiment with fixed claims (quote = its whole `before`)."""

    def __init__(self, ordinary=True, needs_test=False, shape="rewrite_logic", flip_on=None):
        self.ordinary, self.needs_test, self.shape = ordinary, needs_test, shape
        self.flip_on = flip_on          # call number (1-based) on which to invert `ordinary`
        self.calls = 0

    def run(self, prompt, *, cwd, schema, tools=()):
        self.calls += 1
        ordinary = (not self.ordinary) if self.flip_on == self.calls else self.ordinary
        grades = []
        for block in prompt.split("## index ")[1:]:
            idx = int(block.split(" ", 1)[0])
            before = block.split("before:\n```\n", 1)[1].split("\n```", 1)[0]
            grades.append({"index": idx, "changed_code_quote": before, "shape": self.shape,
                           "ordinary_edit": ordinary, "requires_test_knowledge": self.needs_test,
                           "main_path": True, "rationale": "fake"})
        return RunResult({"grades": grades}, 0.01, "")


# --- ⛔ GUARD: the recall matcher ---------------------------------------------------


def test_a_seeded_hit_is_a_hit():
    assert grade.target_hit(SEED, [HIT], _pairs()) is True


def test_a_near_miss_on_the_adjacent_line_is_a_miss():
    assert grade.target_hit(SEED, [NEAR_MISS], _pairs()) is False


def test_the_same_edit_in_another_file_is_a_miss():
    assert grade.target_hit(SEED, [OTHER_FILE_SAME_TEXT], _pairs()) is False


def test_an_experiment_whose_anchor_is_not_unique_reaches_nothing():
    dup = PROD + "\n\ndef again(checked_at, now):\n    if checked_at is None or now - checked_at >= RECHECK:\n        pass\n"
    assert grade.changed_lines(dup, HIT["before"], HIT["after"]) == set()


def test_changed_lines_points_at_the_changed_line_only():
    before = "a = 1\nb = 2\nc = 3"
    content = f"x = 0\n{before}\ny = 9\n"
    assert grade.changed_lines(content, before, "a = 1\nb = 20\nc = 3") == {3}


# --- ⛔ GUARD: the contrived grader --------------------------------------------------

CONTRIVED_FIXTURE = {"guard": "label is stripped", "file": "app/core.py", "kind": "mutation",
                     "before": "    return name.strip()",
                     "after": '    return "" if name == "Fixture Task 7" else name.strip()'}
REALISTIC_FIXTURE = {"guard": "verdict ok", "file": "app/core.py", "kind": "mutation",
                     "before": '    return verdict == "ok"', "after": '    return verdict != "ok"'}


def test_a_contrived_fixture_is_graded_contrived():
    label, shape, _ = grade.classify_programmatic(CONTRIVED_FIXTURE)
    assert (label, shape) == (grade.CONTRIVED, "special_case_injection")


def test_a_realistic_fixture_is_graded_realistic():
    label, shape, _ = grade.classify_programmatic(REALISTIC_FIXTURE)
    assert (label, shape) == (grade.REALISTIC, "flip_condition")


@pytest.mark.parametrize("exp,expected", [
    ({"before": "x = compute()", "after": "x = None"}, grade.REALISTIC),
    ({"before": "if ready and ok:", "after": "if ready or ok:"}, grade.REALISTIC),
    ({"before": "    notify(run)", "after": "    # notify(run)"}, grade.REALISTIC),
    ({"before": "if not stale:", "after": "if False and not stale:"}, grade.REALISTIC),
    ({"before": "return items", "after": "return [] if len(items) == 3 else items"}, grade.CONTRIVED),
])
def test_programmatic_tier_shapes(exp, expected):
    assert grade.classify_programmatic(exp)[0] == expected


def test_ambiguous_shapes_are_left_to_the_llm():
    assert grade.classify_programmatic({"before": 'MSG = "typing is off"',
                                        "after": 'MSG = "typing is of"'})[0] is None
    assert grade.classify_programmatic({**REALISTIC_FIXTURE, "kind": "wiring"})[0] is None


@pytest.mark.parametrize("claims,expected", [
    ({"shape": "narrow_set", "ordinary_edit": True, "requires_test_knowledge": False}, grade.REALISTIC),
    ({"shape": "narrow_set", "ordinary_edit": True, "requires_test_knowledge": True}, grade.CONTRIVED),
    ({"shape": "rewrite_logic", "ordinary_edit": False, "requires_test_knowledge": False}, grade.CONTRIVED),
    ({"shape": "targeted_literal", "ordinary_edit": True, "requires_test_knowledge": False}, grade.CONTRIVED),
])
def test_label_from_claims_applies_the_rubric_rule(claims, expected):
    claims = {**claims, "changed_code_quote": "b = 2"}
    assert grade.label_from_claims(claims, "a = 1\nb = 2")[0] == expected


def test_a_quote_not_in_before_is_ungraded_not_guessed():
    claims = {"changed_code_quote": "nowhere in the code", "shape": "narrow_set",
              "ordinary_edit": True, "requires_test_knowledge": False}
    assert grade.label_from_claims(claims, "a = 1")[0] == grade.UNGRADED


def test_llm_tier_labels_and_counts_a_flip():
    exps = [{"guard": "g", "file": "f.py", "kind": "mutation", "before": 'M = "abc"',
             "after": 'M = "abd"'}]
    grades, cost, errors = grade.grade_contrived(exps, FakeGrader(ordinary=False), "")
    assert [g.label for g in grades] == [grade.CONTRIVED] and not grades[0].flipped
    grades, _, _ = grade.grade_contrived(exps, FakeGrader(ordinary=False, flip_on=2), "")
    assert grades[0].label == grade.CONTRIVED and grades[0].flipped


def test_programmatic_grades_never_reach_the_llm():
    g = FakeGrader()
    grades, _, _ = grade.grade_contrived([REALISTIC_FIXTURE, CONTRIVED_FIXTURE], g, "")
    assert g.calls == 0 and [x.tier for x in grades] == ["programmatic", "programmatic"]


# --- validity uses the judge's own mechanics ---------------------------------------


def test_validity_accepts_a_clean_edit_and_refuses_the_judges_refusals():
    assert grade.check_validity({**HIT, "file": "core.py"}, PROD)[0] is True
    ambiguous = {**HIT, "before": "checked_at"}
    ok, why = grade.check_validity(ambiguous, PROD)
    assert not ok and "occurs" in why
    broken = {**HIT, "after": "    if checked_at is None or:"}
    ok, why = grade.check_validity(broken, PROD)
    assert not ok and "parse" in why
    assert grade.check_validity(HIT, None)[0] is False


# --- ⛔ GUARD: the split never leaks ------------------------------------------------


def test_split_is_a_pure_function_of_the_pr():
    assert {ds.split_for(n) for n in range(1, 400)} == {ds.TRAIN, ds.TEST}
    assert all(ds.split_for(n) == ds.split_for(n) for n in range(50))


def test_every_shipped_case_is_on_its_computed_side():
    cases = ds.load_cases()
    assert cases, "the shipped dataset is empty"
    assert all(c.split == ds.split_for(c.pr) for c in cases)


def test_select_cases_never_returns_the_other_split_even_if_a_row_lies(repo):
    r, base, head = repo
    test_pr = _pr_on(ds.TEST)
    liar = _case(base, head, test_pr, cid="liar")
    liar.split = ds.TRAIN                        # edited to smuggle a test case into train
    assert evaluate.select_cases([liar], ds.TRAIN) == []


def test_a_train_run_never_names_a_test_case(repo):
    r, base, head = repo
    train_pr, test_pr = _pr_on(ds.TRAIN), _pr_on(ds.TEST)
    cases = [_case(base, head, train_pr, cid=f"TRAINCASE-{train_pr}"),
             _case(base, head, test_pr, cid=f"TESTCASE-{test_pr}")]
    lines: list[str] = []
    selected = evaluate.select_cases(cases, ds.TRAIN)
    payload = evaluate.run_eval(r, selected, split=ds.TRAIN, risks=("low",),
                                runner=FakeDesign([HIT]), grader_runner=FakeGrader(),
                                model="m", grader_model="g", confirm=True, jobs=1,
                                out=lines.append)
    text = "\n".join(lines) + json.dumps(payload)
    assert f"TRAINCASE-{train_pr}" in text
    assert f"TESTCASE-{test_pr}" not in text


def test_a_test_run_prints_and_saves_aggregates_only(repo):
    r, base, head = repo
    test_pr = _pr_on(ds.TEST)
    cases = [_case(base, head, test_pr, cid=f"TESTCASE-{test_pr}")]
    lines: list[str] = []
    payload = evaluate.run_eval(r, evaluate.select_cases(cases, ds.TEST), split=ds.TEST,
                                risks=("low",), runner=FakeDesign([HIT]),
                                grader_runner=FakeGrader(), model="m", grader_model="g",
                                confirm=True, jobs=1, out=lines.append)
    text = "\n".join(lines) + json.dumps(payload)
    assert f"TESTCASE-{test_pr}" not in text and "seed-x" not in text
    assert "cases" not in payload and payload["summary"]["low"]["recall_seeded"]["k"] == 1


# --- ⛔ GUARD: the cost estimate comes before any model call ------------------------


def test_the_estimate_is_printed_before_the_first_model_call(repo):
    r, base, head = repo
    lines: list[str] = []
    runner = FakeDesign([HIT], log=lines)
    evaluate.run_eval(r, [_case(base, head, _pr_on(ds.TRAIN))], split=ds.TRAIN, risks=("low",),
                      runner=runner, grader_runner=FakeGrader(), model="m", grader_model="g",
                      confirm=True, jobs=1, out=lines.append)
    assert runner.calls, "the design runner was never called"
    before_first_call = lines[: runner.calls[0]["log_len"]]
    assert any("model call(s)" in line and "$" in line for line in before_first_call)


def test_without_confirmation_nothing_is_spent(repo):
    r, base, head = repo
    runner, grader = FakeDesign([HIT]), FakeGrader()
    lines: list[str] = []
    out = evaluate.run_eval(r, [_case(base, head, _pr_on(ds.TRAIN))], split=ds.TRAIN,
                            risks=("low",), runner=runner, grader_runner=grader, model="m",
                            grader_model="g", confirm=False, out=lines.append)
    assert out is None and runner.calls == [] and grader.calls == 0
    assert any("model call(s)" in line for line in lines)


def test_estimate_counts_design_and_both_grader_runs():
    e = evaluate.estimate(5, 3, "opus", "claude-haiku-4-5")
    assert (e.design_calls, e.grader_calls) == (15, 30) and e.usd > 0


# --- ⭐ controls: an oracle plan scores 1, a null plan 0, the decoy never ------------


def test_oracle_and_null_plans_bracket_the_score(repo):
    r, base, head = repo
    case = _case(base, head, _pr_on(ds.TRAIN))
    oracle = evaluate.score_case(r, case, "high", FakeDesign([HIT]), FakeGrader())
    null = evaluate.score_case(r, case, "high", FakeDesign([NEAR_MISS]), FakeGrader())
    assert (oracle.seeded_hits, oracle.seeded_targets) == (1, 1)
    assert (null.seeded_hits, null.misses) == (0, ["seed-x"])
    assert oracle.decoy_targets == null.decoy_targets == 1
    assert oracle.decoy_hits == null.decoy_hits == 0


def test_historical_recall_counts_real_targets_only(repo):
    r, base, head = repo
    real = ds.Target(id="h-real", file="app/core.py", before=HIT["before"].strip(),
                     after=HIT["after"].strip(), source=ds.HISTORICAL, label=ds.REAL)
    contrived = ds.Target(id="h-contrived", file="app/core.py", before="return name.strip()",
                          after="return name", source=ds.HISTORICAL, label=ds.CONTRIVED)
    case = _case(base, head, _pr_on(ds.TRAIN), targets=[real, contrived], kind=ds.HISTORICAL)
    both = [HIT, {"guard": "s", "file": "app/core.py", "kind": "mutation",
                  "before": "    return name.strip()", "after": "    return name"}]
    res = evaluate.score_case(r, case, "high", FakeDesign(both), FakeGrader())
    assert (res.real_hits, res.real_targets, res.seeded_targets) == (1, 1, 0)
    miss = evaluate.score_case(r, case, "high", FakeDesign([NEAR_MISS]), FakeGrader())
    assert (miss.real_hits, miss.misses) == (0, ["h-real"])


def test_a_plan_that_reaches_the_decoy_is_counted_as_a_decoy_hit(repo):
    """The negative control must be able to FIRE — a count stuck at 0 proves nothing."""
    r, base, head = repo
    decoy_exp = {"guard": "limit", "file": "app/other.py", "kind": "mutation",
                 "before": "LIMIT = 10", "after": "LIMIT = 1"}
    res = evaluate.score_case(r, _case(base, head, _pr_on(ds.TRAIN)), "high",
                              FakeDesign([decoy_exp]), FakeGrader())
    assert (res.decoy_hits, res.seeded_hits) == (1, 0)
    assert report.aggregate([res])["high"]["decoy_hits"]["k"] == 1


def test_a_hit_beyond_the_risk_cap_does_not_count(repo):
    r, base, head = repo
    filler = [{**NEAR_MISS, "guard": f"n{i}"} for i in range(4)]          # low cap = 4
    res = evaluate.score_case(r, _case(base, head, _pr_on(ds.TRAIN)), "low",
                              FakeDesign(filler + [HIT]), FakeGrader())
    assert (res.proposed, res.considered, res.seeded_hits) == (5, 4, 0)


def test_an_invalid_experiment_cannot_score_a_hit(repo):
    r, base, head = repo
    broken_hit = {**HIT, "after": "    if checked_at is None or:"}
    res = evaluate.score_case(r, _case(base, head, _pr_on(ds.TRAIN)), "high",
                              FakeDesign([broken_hit]), FakeGrader())
    assert res.valid == 0 and res.seeded_hits == 0


def test_the_design_tree_is_the_case_head_with_the_diff_beside_it(repo):
    r, base, head = repo
    seen = {}

    class Peek(FakeDesign):
        def run(self, prompt, *, cwd, schema, tools=("Read", "Grep", "Glob")):
            cwd = Path(cwd)
            seen["core"] = (cwd / "app" / "core.py").read_text()
            seen["diff"] = (cwd / ".judge-eval" / "PR_DIFF.patch").read_text()
            seen["pr"] = (cwd / ".judge-eval" / "PR.md").read_text()
            seen["git"] = (cwd / ".git").exists()
            return super().run(prompt, cwd=cwd, schema=schema, tools=tools)

    evaluate.score_case(r, _case(base, head, _pr_on(ds.TRAIN)), "normal", Peek([HIT]),
                        FakeGrader())
    assert seen["core"] == PROD and "+def allowed" in seen["diff"]
    assert "feat: re-check the verdict" in seen["pr"] and seen["git"] is False


# --- the prompt is the LIVE judge prompt, per level --------------------------------


def test_design_prompt_renders_the_live_prompt_at_each_level():
    case = ds.Case(id="c", pr=1, kind=ds.SEEDED, title="t", base_sha="b", head_sha="h",
                   split=ds.TRAIN)
    prompts = {rk: design.render_design_prompt(case, rk) for rk in evaluate.RISKS}
    for rk, text in prompts.items():
        assert judge.RISK_GUIDANCE[rk] in text
        assert "OFFLINE EVAL MODE" in text and "{{" not in text
        assert "gh pr view" not in text          # the PR's comments would leak the answers
    assert len(set(prompts.values())) == 3


def test_a_candidate_template_replaces_the_live_one():
    case = ds.Case(id="c", pr=1, kind=ds.SEEDED, title="t", base_sha="b", head_sha="h",
                   split=ds.TRAIN)
    text = design.render_design_prompt(case, "low", "CANDIDATE {{risk}} {{max_experiments}}")
    assert text.startswith("CANDIDATE low 4")


def test_the_cli_runner_is_read_only_and_capped(monkeypatch):
    monkeypatch.setenv("CHELA_ACTOR", "orchestrator")
    cmd = ClaudeCLIRunner("opus", max_budget_usd=1.5).command("p", {"type": "object"},
                                                              ("Read", "Grep", "Glob"))
    assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob"
    assert "--restricted" in cmd and cmd[cmd.index("--max-budget-usd") + 1] == "1.50"
    assert "CHELA_ACTOR" not in ClaudeCLIRunner.env()


# --- mining round-trips the judge's own renderer -----------------------------------


def test_parse_findings_reads_back_what_block_body_wrote():
    exp = judge.Experiment(guard="a guard", file="chela/x.py",
                           before="    if a and b:\n        go()", after="    if a:\n        go()",
                           kind="wiring")
    rep = judge.Report(outcomes=[judge.Outcome(experiment=exp, verdict=judge.SURVIVED,
                                               reason="green", parse_detail="parses")])
    found = mine.parse_findings(judge.block_body(rep, "https://example.com/pull/1", "pytest"))
    assert [(f.guard, f.file, f.kind, f.before, f.after) for f in found] == \
        [("a guard", "chela/x.py", "wiring", exp.before, exp.after)]
    assert mine.verdict_of(judge.block_body(rep, None, "pytest")) == "blocked"


def test_head_at_picks_the_newest_commit_before_the_verdict():
    commits = [{"sha": "a", "commit": {"committer": {"date": "2026-09-01T00:00:00Z"}}},
               {"sha": "b", "commit": {"committer": {"date": "2026-09-02T00:00:00Z"}}},
               {"sha": "c", "commit": {"committer": {"date": "2026-09-03T00:00:00Z"}}}]
    assert mine.head_at(commits, "2026-09-02T12:00:00Z") == "b"


def test_first_verdicts_skip_cannot_verify_and_keep_the_earliest():
    rows = [{"pr": 500, "verdict": "cannot_verify", "created_at": "1"},
            {"pr": 500, "verdict": "blocked", "created_at": "2"},
            {"pr": 500, "verdict": "clean", "created_at": "3"},
            {"pr": 10, "verdict": "blocked", "created_at": "1"}]
    firsts = mine.first_verdicts(rows, min_pr=400)
    assert list(firsts) == [500] and firsts[500]["created_at"] == "2"


# --- ⛔ public repo: the shipped dataset carries nothing private --------------------


def test_the_shipped_dataset_has_no_private_data():
    committed = sorted((Path(__file__).parent.parent / "docs" / "judge_eval").glob("*"))
    for path in (ds.CASES_PATH, ds.SEEDS_PATH, evaluate.CONTROLS_PATH, *committed):
        text = path.read_text()
        assert not mine.PRIVATE_RE.search(text), path
    assert mine.is_private("see /home/someone/x") and mine.is_private("ghp_" + "a" * 30)


def test_every_shipped_target_locates_code_at_its_head():
    """Each target's anchor must be unique at its case head — else it can never be hit."""
    repo = ds.repo_root(Path(__file__).parent)
    for c in ds.load_cases():
        if c.kind != ds.SEEDED or not ds.has_commit(repo, c.head_sha):
            continue
        for t in c.targets:
            content = ds.show_file(repo, c.head_sha, t.file)
            assert content is not None and content.count(t.before) == 1, t.id


# --- report --------------------------------------------------------------------------


def test_wilson_is_sane_at_the_edges():
    assert report.wilson(0, 0) is None
    lo, hi = report.wilson(0, 10)
    assert lo == 0.0 and 0.2 < hi < 0.35
    lo, hi = report.wilson(10, 10)
    assert hi == 1.0 and lo > 0.65


def test_spot_check_sheet_samples_train_only_and_agreement_scores_it():
    tr, te = _pr_on(ds.TRAIN), _pr_on(ds.TEST)

    def mk(pr, i, label):
        return ds.Target(id=f"pr{pr}-f{i}", file="a.py", before="x", after="y",
                         source=ds.HISTORICAL, guard="g", label=label)

    cases = [ds.Case(id=f"pr{tr}", pr=tr, kind=ds.HISTORICAL, title="", base_sha="", head_sha="",
                     split=ds.TRAIN, targets=[mk(tr, i, ds.REAL) for i in range(10)]),
             ds.Case(id=f"pr{te}", pr=te, kind=ds.HISTORICAL, title="", base_sha="", head_sha="",
                     split=ds.TEST, targets=[mk(te, i, ds.REAL) for i in range(10)])]
    sheet = evaluate.spot_check_sheet(cases)
    assert f"pr{te}-" not in sheet and sheet.count(f"`pr{tr}-") == 2
    rows = sheet.splitlines()
    rows[2] = rows[2].replace("| real |  |", "| real | real |")
    rows[3] = rows[3].replace("| real |  |", "| real | contrived |")
    a = evaluate.agreement("\n".join(rows))
    assert (a["rows"], a["filled"], a["agree"]) == (2, 2, 1)
