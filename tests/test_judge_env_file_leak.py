"""⚖️ CMX-357 — the judge's own real ``chela.env`` must never leak into the suite it runs.

⛔ THE BUG THIS FILE EXISTS FOR (measured 2026-09-10, chasing this ticket's own
``chela judge self-check``). ``chela.config`` sources ``$CHELA_DIR/chela.env`` at IMPORT
time (``load_env_file``'s ``os.environ.setdefault`` — the operator's real config file, one
line per knob). The judge process is itself an ordinary chela process, so importing
``chela.config`` has ALREADY sourced that file into the judge's OWN ``os.environ`` before
:func:`chela.judge._no_color_env` ever runs — and that function used to build the suite
subprocess's environment with a bare ``dict(os.environ)``, copying every operator-set knob
straight into the run.

``tests/conftest.py`` goes to real lengths (``CHELA_ENV_FILE=""``) to keep a developer's
real ``chela.env`` out of a TEST PROCESS — but that guard runs inside the suite subprocess,
which never re-reads the file itself. It cannot protect against a variable that was already
sitting in the ENVIRONMENT the subprocess inherited: a real env var, indistinguishable at
that point from something the operator actually exported. Measured live: this box's own
``~/.chela/chela.env`` sets ``CHELA_RESTORE_RESUME=true`` (a deliberate, correct choice for
the DAEMON), and every suite run launched through ``chela judge self-check`` /
``chela task-finished`` inherited it — failing
``test_chela_restore_resume_is_disabled_by_default_falls_back_to_read_only`` for a reason
that had nothing to do with the PR under judgment: the judge's own box, not the code.

Same root cause and the same fix shape as CMX-252's ``NODE_CHANNEL_FD``
(:mod:`tests.test_judge_node_channel_fd`) — "the judge's own inherited environment is not
the PR's suite to run under uncritically" — generalised from two hardcoded keys to every key
:func:`chela.config.load_env_file` populated via ``setdefault``.
"""
from __future__ import annotations

from chela import config, judge


def test_no_color_env_strips_every_key_the_operators_chela_env_declared(monkeypatch):
    # Simulate what `load_env_file` already did to the judge's own `os.environ` at import —
    # this test does not re-read a real file, it pins the RECORD of what one declared
    # (`config.ENV_FILE_VARS`), exactly what `_no_color_env` consults.
    monkeypatch.setattr(config, "ENV_FILE_VARS", {"CHELA_RESTORE_RESUME": "true"})
    monkeypatch.setenv("CHELA_RESTORE_RESUME", "true")

    assert "CHELA_RESTORE_RESUME" not in judge._no_color_env()


def test_no_color_env_leaves_a_genuine_export_with_no_matching_file_key_alone(monkeypatch):
    # An operator's real `export FOO=bar` (nothing to do with chela.env) must still reach
    # the suite — this fix only strips keys the FILE declared, never the whole environment.
    monkeypatch.setattr(config, "ENV_FILE_VARS", {})
    monkeypatch.setenv("SOME_UNRELATED_EXPORT", "bar")

    assert judge._no_color_env().get("SOME_UNRELATED_EXPORT") == "bar"


def test_no_color_env_is_a_no_op_when_the_env_file_declared_nothing(monkeypatch):
    monkeypatch.setattr(config, "ENV_FILE_VARS", {})
    env = judge._no_color_env()
    assert env.get("NO_COLOR") == "1"          # the function's existing baseline still runs
