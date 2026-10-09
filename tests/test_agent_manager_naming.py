"""Window-name locking — the dashboard tile "name flicker" fix.

A managed claude window's tile name is its tmux window name. tmux has TWO
mechanisms that can overwrite it: ``allow-rename`` (an OSC title escape) and
``automatic-rename`` (following ``pane_current_command``). The latter is the
flicker: the name follows ``git`` / ``node`` / ``bash`` the instant claude
shells out, then snaps back — verified live that ``automatic-rename on`` +
``allow-rename off`` still drifts the name to the subcommand.

Chela used to set only ``allow-rename off`` and rely on ``rename-window`` /
``new-window -n`` to disable ``automatic-rename`` as a side effect. A window that
reached us already-correctly-named (hand-started, never renamed by us) kept the
default ``automatic-rename on`` and flickered forever, because reconcile
``continue``-d past the lock whenever the name already matched. These lock in the
belt-and-suspenders fix: BOTH options are pinned, and reconcile asserts the lock
even when no rename is needed.

CMX-62 on top: reconcile renames a window ONLY to resolve a duplicate (the newer
one gets ``-N``; a manual ``@chela_manual_name`` window is never the one renamed),
and never rewrites a unique name to its cwd basename any more.

Exercised against a synthetic tmux via monkeypatched ``subprocess.run`` — no live
tmux.
"""
import subprocess
import types

from chela import agent_manager, config


class _FakeTmux:
    """Records every ``tmux`` argv and scripts ``list-windows`` output."""

    def __init__(self, list_output: str = ""):
        self.list_output = list_output
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:2] == ["tmux", "list-windows"]:
            return types.SimpleNamespace(returncode=0, stdout=self.list_output, stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def set_options(self) -> dict[str, str]:
        """``{option: value}`` from every ``set-window-option`` call."""
        return {
            c[4]: c[5] for c in self.calls if c[:2] == ["tmux", "set-window-option"]
        }

    def renames(self) -> list[str]:
        """Target names from every ``rename-window`` call."""
        return [c[-1] for c in self.calls if c[:2] == ["tmux", "rename-window"]]


def _row(wid, name, cmd, idx, auto, allow, manual=""):
    """One ``list-windows`` line in reconcile's format (id, name, cmd, index, locks, manual)."""
    return "\t".join([wid, name, cmd, str(idx), auto, allow, manual])


def _rows(*rows) -> str:
    return "".join(_row(*r) + "\n" for r in rows)


def _reconcile(monkeypatch, *rows):
    fake = _FakeTmux(_rows(*rows))
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")
    return agent_manager.reconcile_window_names(), fake


# --- the lock primitive -------------------------------------------------------

def test_lock_window_name_disables_both_rename_mechanisms(monkeypatch):
    fake = _FakeTmux()
    monkeypatch.setattr(subprocess, "run", fake)

    agent_manager.lock_window_name("@7")

    # BOTH mechanisms are pinned off — allow-rename alone left the flicker open.
    assert fake.set_options() == {"allow-rename": "off", "automatic-rename": "off"}
    assert all("@7" in c for c in fake.calls)


# --- start path ---------------------------------------------------------------

def test_start_agent_keeps_the_window_name_and_locks_it(monkeypatch, tmp_path):
    # CMX-62: Start no longer renames a generic window to its cwd — it only locks.
    fake = _FakeTmux()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")
    monkeypatch.setattr(agent_manager, "get_window_id", lambda n: "@9")
    monkeypatch.setattr(agent_manager, "is_claude_running", lambda w: False)
    monkeypatch.setattr(agent_manager, "_resolve_start_dir", lambda n: str(tmp_path))
    monkeypatch.setattr(agent_manager, "send_tmux", lambda *a, **k: True)

    assert agent_manager.start_agent("shell-2")["ok"] is True
    assert fake.renames() == []
    assert fake.set_options() == {"allow-rename": "off", "automatic-rename": "off"}


# --- reconcile: the lock -------------------------------------------------------

def test_reconcile_locks_an_unlocked_claude_window_without_renaming(monkeypatch):
    # The flicker fix: a claude window still on automatic-rename=on is pinned —
    # with NO rename.
    actions, fake = _reconcile(monkeypatch, ("@9", "nautilus", "claude", 1, "1", "0"))
    assert actions == []
    assert fake.renames() == []
    assert fake.set_options() == {"allow-rename": "off", "automatic-rename": "off"}


def test_reconcile_leaves_a_locked_unique_window_untouched(monkeypatch):
    # Steady state: no tmux call beyond the one list-windows read — no per-tick churn.
    actions, fake = _reconcile(monkeypatch, ("@9", "nautilus", "claude", 1, "0", "0"))
    assert actions == []
    assert [c[:2] for c in fake.calls] == [["tmux", "list-windows"]]


def test_reconcile_skips_locking_a_non_claude_pane(monkeypatch):
    actions, fake = _reconcile(monkeypatch, ("@9", "shell-1", "bash", 1, "1", "1"))
    assert actions == []
    assert fake.set_options() == {}
    assert fake.renames() == []


# --- CMX-62: a unique name is never rewritten ----------------------------------

def test_reconcile_keeps_a_unique_shell_name(monkeypatch):
    # THE defect: `chela spawn ~/projects/tradeplan` made `shell-1`, and one tick
    # later reconcile renamed it to its cwd basename. A unique name stays — across
    # passes, with no action emitted.
    fake = _FakeTmux(_rows(("@13", "shell-1", "claude", 13, "0", "0"),
                           ("@6", "nautilus-old", "claude", 6, "0", "0")))
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")
    for _ in range(3):
        assert agent_manager.reconcile_window_names() == []
    assert fake.renames() == []


def test_reconcile_keeps_a_drifted_unique_name(monkeypatch):
    # A window that drifted (to `git`) before it was locked stays as-is; it is
    # locked so it can't drift further, but not renamed.
    actions, fake = _reconcile(monkeypatch, ("@9", "git", "claude", 1, "1", "1"))
    assert actions == []
    assert fake.renames() == []
    assert fake.set_options() == {"allow-rename": "off", "automatic-rename": "off"}


def test_reconcile_keeps_a_raw_tmux_renamed_unique_name(monkeypatch):
    # A raw `tmux rename-window` (no manual flag) is not detected as manual — but a
    # unique name is never rewritten regardless.
    actions, fake = _reconcile(monkeypatch, ("@9", "billing-fix", "claude", 1, "0", "0"))
    assert actions == []
    assert fake.renames() == []


# --- CMX-62: duplicates --------------------------------------------------------

def test_reconcile_renames_the_newer_duplicate_and_keeps_the_older(monkeypatch):
    # Listed newest-first on purpose: age is the window INDEX, not list order.
    actions, fake = _reconcile(monkeypatch,
                               ("@13", "shell-1", "claude", 7, "0", "0"),
                               ("@4", "shell-1", "bash", 2, "0", "0"))
    assert actions == ["shell-1 -> shell-1-2"]
    renames = [(c[3], c[4]) for c in fake.calls if c[:2] == ["tmux", "rename-window"]]
    assert renames == [("sess:@13", "shell-1-2")]       # the NEWER one; @4 untouched


def test_reconcile_duplicate_suffix_is_collision_safe(monkeypatch):
    actions, _ = _reconcile(monkeypatch,
                            ("@1", "tradeplan", "claude", 1, "0", "0"),
                            ("@2", "tradeplan-2", "claude", 2, "0", "0"),
                            ("@3", "tradeplan", "claude", 3, "0", "0"))
    assert actions == ["tradeplan -> tradeplan-3"]


def test_reconcile_basic_duplicate_gets_dash_two(monkeypatch):
    actions, _ = _reconcile(monkeypatch,
                            ("@1", "tradeplan", "claude", 1, "0", "0"),
                            ("@2", "tradeplan", "claude", 2, "0", "0"))
    assert actions == ["tradeplan -> tradeplan-2"]


def test_reconcile_manual_name_wins_a_duplicate_over_an_older_window(monkeypatch):
    # The NEWER window is the manual one — it still keeps the name; the older,
    # non-manual window is the one suffixed.
    actions, fake = _reconcile(monkeypatch,
                               ("@1", "billing", "claude", 1, "0", "0"),
                               ("@2", "billing", "claude", 2, "0", "0", "1"))
    assert actions == ["billing -> billing-2"]
    renames = [(c[3], c[4]) for c in fake.calls if c[:2] == ["tmux", "rename-window"]]
    assert renames == [("sess:@1", "billing-2")]


def test_reconcile_never_renames_two_colliding_manual_names(monkeypatch, caplog):
    with caplog.at_level("WARNING", logger="chela.agent_manager"):
        actions, fake = _reconcile(monkeypatch,
                                   ("@1", "billing", "claude", 1, "0", "0", "1"),
                                   ("@2", "billing", "claude", 2, "0", "0", "1"))
    assert actions == []
    assert fake.renames() == []
    assert any("billing" in r.getMessage() and r.levelname == "WARNING"
               for r in caplog.records)


def test_reconcile_never_renames_dispatch_or_judge_windows(monkeypatch):
    actions, fake = _reconcile(monkeypatch,
                               ("@1", "judge-x/cmx-1", "claude", 1, "0", "0"),
                               ("@2", "judge-x/cmx-1", "claude", 2, "0", "0"),
                               ("@3", "x/cmx-2-fix", "claude", 3, "0", "0"),
                               ("@4", "x/cmx-2-fix", "claude", 4, "0", "0"))
    assert actions == []
    assert fake.renames() == []


# --- the manual flag -------------------------------------------------------------

def test_mark_manual_name_sets_the_window_user_option(monkeypatch):
    fake = _FakeTmux()
    monkeypatch.setattr(subprocess, "run", fake)
    agent_manager.mark_manual_name("sess:@7")
    assert fake.calls == [["tmux", "set-window-option", "-t", "sess:@7",
                           agent_manager.MANUAL_NAME_OPTION, "1"]]


def test_manual_name_wids_reads_the_flag(monkeypatch):
    fake = _FakeTmux("@1\t1\n@2\t\n@3\t0\n")
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")
    assert agent_manager.manual_name_wids() == {"@1"}


def test_is_generic_name_classifies_placeholders_vs_chosen_names():
    # Still the Telegram topic-naming hint (not a rename trigger any more).
    for placeholder in ("shell", "shell-1", "shell-42", "SHELL-2", "bash", "zsh",
                        "claude", "node", "python3", "", "   "):
        assert agent_manager.is_generic_name(placeholder) is True, placeholder
    for chosen in ("billing-fix", "chelamux", "nautilus", "shell-fix", "my-shell"):
        assert agent_manager.is_generic_name(chosen) is False, chosen
