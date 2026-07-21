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


def _row(wid, name, cmd, cwd, auto, allow):
    return "\t".join([wid, name, cmd, cwd, auto, allow])


# --- the lock primitive -------------------------------------------------------

def test_lock_window_name_disables_both_rename_mechanisms(monkeypatch):
    fake = _FakeTmux()
    monkeypatch.setattr(subprocess, "run", fake)

    agent_manager.lock_window_name("@7")

    # BOTH mechanisms are pinned off — allow-rename alone left the flicker open.
    assert fake.set_options() == {"allow-rename": "off", "automatic-rename": "off"}
    assert all("@7" in c for c in fake.calls)


# --- start path ---------------------------------------------------------------

def test_name_window_to_cwd_locks_after_rename(monkeypatch):
    fake = _FakeTmux()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")
    monkeypatch.setattr(agent_manager, "get_all_windows", lambda: {})

    name = agent_manager._name_window_to_cwd("@9", "/home/x/projects/nautilus")

    assert name == "nautilus"
    assert fake.renames() == ["nautilus"]
    assert fake.set_options() == {"allow-rename": "off", "automatic-rename": "off"}


# --- reconcile: the flicker case ---------------------------------------------

def test_reconcile_locks_already_named_window_with_automatic_rename_on(monkeypatch):
    # The regression: a claude window already named after its cwd but still on the
    # tmux default automatic-rename=on. Old code skipped it (name == base) before
    # ever locking, so it flickered on every subcommand. It must now be locked —
    # with NO rename (the name is already right).
    fake = _FakeTmux(_row("@9", "nautilus", "claude",
                          "/home/x/projects/nautilus", "1", "0") + "\n")
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")

    actions = agent_manager.reconcile_window_names()

    assert actions == []                      # already correctly named — no rename
    assert fake.renames() == []
    assert fake.set_options() == {"allow-rename": "off", "automatic-rename": "off"}


def test_reconcile_leaves_locked_correctly_named_window_untouched(monkeypatch):
    # Steady state: name matches AND both mechanisms already off. Reconcile must
    # not touch tmux at all (beyond the one list-windows read) — no per-tick churn.
    fake = _FakeTmux(_row("@9", "nautilus", "claude",
                          "/home/x/projects/nautilus", "0", "0") + "\n")
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")

    actions = agent_manager.reconcile_window_names()

    assert actions == []
    assert fake.set_options() == {}           # nothing to lock
    assert fake.renames() == []
    assert [c[:2] for c in fake.calls] == [["tmux", "list-windows"]]


def test_reconcile_renames_and_locks_a_drifted_window(monkeypatch):
    # A window whose name drifted to the subcommand (git) with both mechanisms
    # live: reconcile renames it back to the cwd basename AND pins both locks.
    fake = _FakeTmux(_row("@9", "git", "claude",
                          "/home/x/projects/nautilus", "1", "1") + "\n")
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")

    actions = agent_manager.reconcile_window_names()

    assert actions == ["git -> nautilus"]
    assert fake.renames() == ["nautilus"]
    assert fake.set_options() == {"allow-rename": "off", "automatic-rename": "off"}


def test_reconcile_skips_non_claude_pane(monkeypatch):
    # A plain shell (no claude) is never renamed or locked — reconcile only
    # manages live claude sessions.
    fake = _FakeTmux(_row("@9", "shell-1", "bash",
                          "/home/x/projects/nautilus", "1", "1") + "\n")
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")

    actions = agent_manager.reconcile_window_names()

    assert actions == []
    assert fake.set_options() == {}
    assert fake.renames() == []

# --- deliberate names: the auto-namers fill in blanks, they never override intent -

def test_is_generic_name_classifies_placeholders_vs_chosen_names():
    # Blanks chela may auto-manage: its own shell-N placeholders, and the names
    # tmux's automatic-rename derives from the running command.
    for placeholder in ("shell", "shell-1", "shell-42", "SHELL-2", "bash", "zsh",
                        "claude", "node", "python3", "", "   "):
        assert agent_manager.is_generic_name(placeholder) is True, placeholder
    # Anything else was chosen by a human and is off-limits to the auto-namers.
    for chosen in ("billing-fix", "chelamux", "nautilus", "shell-fix", "my-shell"):
        assert agent_manager.is_generic_name(chosen) is False, chosen


def test_reconcile_does_not_revert_a_user_renamed_window(monkeypatch):
    # THE regression. reconcile_window_names() renamed every claude window whose
    # name != its cwd basename, on every 30s daemon tick — so a deliberate rename
    # (dashboard, or `tmux rename-window`) was silently reverted within 30s and the
    # tmux name could never be the source of truth. A chosen name must SURVIVE a
    # tick. The name is LOCKED (automatic-rename off, as both `tmux rename-window`
    # and the rename endpoint leave it) — that lock is what marks it deliberate.
    fake = _FakeTmux(_row("@9", "billing-fix", "claude",
                          "/home/x/projects/nautilus", "0", "0") + "\n")
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")

    actions = agent_manager.reconcile_window_names()

    assert actions == []                      # NOT renamed back to "nautilus"
    assert fake.renames() == []
    assert [c[:2] for c in fake.calls] == [["tmux", "list-windows"]]   # no churn


def test_reconcile_still_corrects_a_command_drifted_name(monkeypatch):
    # The counterpart: automatic-rename is ON, so the name is tmux's doing (it
    # follows the running command — "git" here), not a human's. That is a blank, and
    # it is auto-corrected + locked, so drift can't come back.
    fake = _FakeTmux(_row("@9", "billing-fix", "claude",
                          "/home/x/projects/nautilus", "1", "1") + "\n")
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")

    assert agent_manager.reconcile_window_names() == ["billing-fix -> nautilus"]
    assert fake.set_options() == {"allow-rename": "off", "automatic-rename": "off"}


def test_reconcile_still_names_a_generic_window_after_the_rename_fix(monkeypatch):
    # The other half of the contract: a window still carrying a placeholder name is
    # a blank, so reconcile fills it in from the cwd exactly as before.
    fake = _FakeTmux(_row("@9", "shell-3", "claude",
                          "/home/x/projects/nautilus", "1", "1") + "\n")
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")

    assert agent_manager.reconcile_window_names() == ["shell-3 -> nautilus"]
    assert fake.renames() == ["nautilus"]


def test_start_agent_rename_keeps_a_deliberate_window_name(monkeypatch):
    # Starting claude in a window a human named must not clobber that name either.
    fake = _FakeTmux()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")
    monkeypatch.setattr(agent_manager, "get_all_windows", lambda: {"billing-fix": "@9"})

    assert agent_manager._name_window_to_cwd("@9", "/home/x/projects/nautilus") is None
    assert fake.renames() == []


def test_start_agent_rename_still_fills_in_a_generic_window_name(monkeypatch):
    fake = _FakeTmux()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(config, "current_session", lambda: "sess")
    monkeypatch.setattr(agent_manager, "get_all_windows", lambda: {"shell-2": "@9"})

    assert agent_manager._name_window_to_cwd("@9", "/home/x/projects/nautilus") == "nautilus"
    assert fake.renames() == ["nautilus"]
