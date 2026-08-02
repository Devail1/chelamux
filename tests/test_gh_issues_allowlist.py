"""`gh_issues` must not turn every open issue into an agent on the operator's machine.

Before `require_label`, `GhIssuesSource.list_open_tasks()` returned EVERY open issue and
the only filter was the `blocked_label` *exclusion*. Each task it yields becomes an
autonomously dispatched Claude Code agent, in a git worktree, on the operator's box, with
the operator's `gh` credentials — so on a public repo, anyone who could open an issue
could run code there.

⭐ The gate is a required label because **applying a label needs write/triage permission**.
An outsider can open an issue; they cannot label it. GitHub enforces the check, so this is
authorization rather than convention.

The other half is that being unconfigured fails CLOSED *and says so*. An empty task list on
its own is indistinguishable from "no open issues" and from a broken `gh` call — the silent
stall this project keeps re-learning — so the refusal is asserted on its TEXT, in the log and
in `chela doctor`, not merely on the absence of tasks.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from chela.sources import gh_issues
from chela.sources.gh_issues import GhIssuesSource


def _wf(tmp_path, **tracker):
    """A WorkflowDef-alike: only `.path` and `.get('tracker', key)` are used."""
    cfg = {"tracker": {"kind": "gh_issues", "repo": "acme/widgets", **tracker}}

    def get(*keys, default=None):
        cur = cfg
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    return SimpleNamespace(path=tmp_path / "WORKFLOW.md", get=get, config=cfg)


ISSUES = [
    {"number": 1, "title": "labelled by a maintainer", "url": "u1",
     "labels": [{"name": "ready-for-agent"}], "author": {"login": "maintainer"}},
    {"number": 2, "title": "opened by a stranger, unlabelled", "url": "u2",
     "labels": [], "author": {"login": "stranger"}},
    {"number": 3, "title": "labelled but also blocked", "url": "u3",
     "labels": [{"name": "ready-for-agent"}, {"name": "blocked"}],
     "author": {"login": "maintainer"}},
]


@pytest.fixture(autouse=True)
def _no_cross_test_log_dedupe():
    """`_report_once` is process-global by design (one ERROR per repo per process, not
    per tick). Clear it between tests or the second test to look at the log sees nothing."""
    gh_issues._reported.clear()
    yield
    gh_issues._reported.clear()


@pytest.fixture
def fake_gh(monkeypatch):
    """Stand in for `gh issue list`, and record the argv so the `author` field — which the
    trusted_authors gate needs — is asserted to be REQUESTED, not just handled."""
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(ISSUES), stderr="")

    monkeypatch.setattr(gh_issues.subprocess, "run", run)
    return calls


def _titles(tasks):
    return {t.title for t in tasks}


def test_an_unlabelled_issue_is_never_claimed(tmp_path, fake_gh):
    """THE FIX. Issue #2 is a stranger's unlabelled issue — the attack case."""
    src = GhIssuesSource(_wf(tmp_path, require_label="ready-for-agent"))
    assert "opened by a stranger, unlabelled" not in _titles(src.list_open_tasks())


def test_a_labelled_issue_IS_claimed(tmp_path, fake_gh):
    """⭐ COUNTERWEIGHT. Without this, an implementation that claims NOTHING passes the
    test above, and a gate that blocks everything is not a gate — it is an outage."""
    src = GhIssuesSource(_wf(tmp_path, require_label="ready-for-agent"))
    tasks = src.list_open_tasks()
    assert "labelled by a maintainer" in _titles(tasks)
    assert [t.line_number for t in tasks if t.title == "labelled by a maintainer"] == [1]


def test_the_blocked_label_still_excludes_a_labelled_issue(tmp_path, fake_gh):
    """NEGATIVE CONTROL / regression: the pre-existing exclusion composes with the new
    gate rather than being replaced by it. Issue #3 carries BOTH labels."""
    src = GhIssuesSource(_wf(tmp_path, require_label="ready-for-agent"))
    assert "labelled but also blocked" not in _titles(src.list_open_tasks())


def test_unconfigured_refuses_and_SAYS_SO(tmp_path, fake_gh, caplog):
    """⛔ Fail closed AND loud. Asserts the message TEXT: an empty list alone is equally
    satisfied by 'no open issues' and by a broken `gh` call, so absence proves nothing."""
    src = GhIssuesSource(_wf(tmp_path))                     # no require_label at all
    with caplog.at_level(logging.ERROR, logger=gh_issues.log.name):
        assert src.list_open_tasks() == []
    assert src.config_error and "require_label" in src.config_error
    assert any("require_label" in r.getMessage() for r in caplog.records), (
        "refusing to claim work must be stated, not silent"
    )


def test_unconfigured_never_calls_gh_at_all(tmp_path, fake_gh):
    """The refusal happens BEFORE the API call — a gate that only filters results still
    depends on the fetch succeeding, and a failed fetch would look identical to a refusal."""
    GhIssuesSource(_wf(tmp_path)).list_open_tasks()
    assert fake_gh == []


def test_an_explicit_false_opts_out_deliberately(tmp_path, fake_gh):
    """`require_label: false` is a recorded choice, distinct from the key being absent —
    which is why the code needs a sentinel and not `default=None`."""
    src = GhIssuesSource(_wf(tmp_path, require_label=False))
    assert src.config_error is None
    assert "opened by a stranger, unlabelled" in _titles(src.list_open_tasks())


def test_an_empty_require_label_is_a_config_error_not_an_open_gate(tmp_path, fake_gh):
    """`require_label: ""` must not silently mean "no gate" — that is the insecure default
    wearing a disguise."""
    src = GhIssuesSource(_wf(tmp_path, require_label="   "))
    assert src.list_open_tasks() == []
    assert src.config_error


def test_trusted_authors_rejects_a_labelled_issue_from_an_unexpected_login(tmp_path, fake_gh):
    src = GhIssuesSource(_wf(
        tmp_path, require_label="ready-for-agent", trusted_authors=["someone-else"],
    ))
    assert _titles(src.list_open_tasks()) == set()


def test_trusted_authors_accepts_the_configured_login(tmp_path, fake_gh):
    """COUNTERWEIGHT for the author gate."""
    src = GhIssuesSource(_wf(
        tmp_path, require_label="ready-for-agent", trusted_authors=["maintainer"],
    ))
    assert "labelled by a maintainer" in _titles(src.list_open_tasks())


def test_doctor_reports_a_refusing_tracker_as_an_ERROR():
    """The standing signal. The log line is once-per-process by design, so `doctor` is
    what a human sees on the tenth day of a stopped queue — assert the SEVERITY and that
    the reason survives into the finding, not merely that some finding exists."""
    from chela import runtime_truth
    from chela.doctor import ERROR

    # `tracker`/`project` are carried deliberately: without them, deleting the
    # `refusing` branch would make this test die on a KeyError from the healthy-path
    # fallback — reddening for a reason that has nothing to do with the severity being
    # asserted, and passing again the moment that branch learned `.get()`. With them,
    # the fallback yields a perfectly valid OK finding and the ERROR assertion is what
    # actually fails.
    obs = runtime_truth.observed([{
        "path": __import__("pathlib").Path("/repo/WORKFLOW.md"),
        "state": "refusing",
        "detail": "tracker.require_label is not set",
        "tracker": None,
        "project": "PROJ",
    }])
    findings = runtime_truth._workflows_report([], obs)
    assert [f.level for f in findings] == [ERROR]
    assert "require_label" in findings[0].detail


def test_the_author_field_is_actually_requested(tmp_path, fake_gh):
    """The author gate reads `issue['author']['login']`; if the field is not asked for,
    every login reads as None and the gate would reject everything — or, if the check were
    written the other way, accept everything. Assert the request, not just the handling."""
    GhIssuesSource(_wf(tmp_path, require_label="ready-for-agent")).list_open_tasks()
    assert fake_gh, "expected one `gh issue list` call"
    argv = fake_gh[0]
    fields = argv[argv.index("--json") + 1]
    assert "author" in fields.split(",")
