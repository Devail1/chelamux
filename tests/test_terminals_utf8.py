"""The wall's tmux client must be spawned UTF-8, or the TUI arrives as underscores.

`scripts/agent-terminals.sh` runs one ttyd per agent window, and ttyd runs a tmux CLIENT
to attach to the pane. PM2 starts that supervisor with a bare environment — no `LANG`, no
`LC_ALL`, no `LC_CTYPE` — so tmux's own locale check marks the client non-UTF-8
(`#{client_utf8}` = 0), and a non-UTF-8 tmux client **substitutes an ASCII `_` for every
non-ASCII character** before writing to the terminal.

Measured on macOS 23.6 before the fix: Claude Code's TUI markers reached xterm as literal
0x5F underscores — `_ Baked for 10s`, `_ Stop Task` — while `tmux capture-pane` on the same
window showed the real `✻`/`⏺`. The pane was never wrong; the client was.

The failure mode is why this test exists at all: it is INDISTINGUISHABLE from missing glyph
coverage. It sent three font fixes chasing it (CMX-155/156/158/159 — real bugs, but on the
`/screenshot` PNG and collab-viewer paths), and no font can ever fix it, because the
character is replaced two layers upstream of the browser and the web terminal's bundled
faces never see it. Dropping `-u` would silently reopen that hunt, so it is asserted here
rather than trusted to a comment.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_TERMINALS_SH = ROOT / "scripts" / "agent-terminals.sh"


def _spawn_tmux_invocation() -> str:
    """The `tmux …` ttyd is told to run — the client, not the server-side helpers."""
    sh = AGENT_TERMINALS_SH.read_text()
    m = re.search(r"^\s*tmux\b.*?new-session\s+-A\s+-s\s+\"\$\{grp\}\"", sh, re.M)
    assert m, "could not find the ttyd tmux client invocation in agent-terminals.sh"
    return m.group(0)


def test_the_ttyd_tmux_client_is_forced_utf8():
    """`tmux -u`, before the subcommand. Without it the client inherits PM2's bare env,
    tmux decides it is not UTF-8, and every TUI glyph becomes `_` in the browser."""
    invocation = _spawn_tmux_invocation()
    assert re.match(r"^\s*tmux\s+(-\w+\s+)*-u\b", invocation), (
        "the ttyd tmux client must be spawned as `tmux -u new-session …` — without -u a "
        f"non-UTF-8 client rewrites every non-ASCII char to '_'. Found: {invocation!r}"
    )


def test_the_reason_for_u_is_written_down_next_to_it():
    """A bare `-u` reads like a typo and invites a 'cleanup'. The comment is the only thing
    that tells the next reader this is not a font bug — keep them together."""
    sh = AGENT_TERMINALS_SH.read_text()
    idx = sh.index("tmux -u new-session")
    preamble = sh[max(0, idx - 1200):idx]
    assert "client_utf8" in preamble or "UTF-8" in preamble, (
        "explain -u where it is used: why a non-UTF-8 tmux client mangles the TUI"
    )


def test_the_anchor_session_is_not_confused_for_the_client():
    """The supervisor also creates the SHARED session (`new-session -A -d`) when it has
    gone missing. That one is a detached server-side helper writing to no terminal, so it
    needs no `-u`; this test pins that the assertion above is about the ttyd client and
    does not silently start passing because some other tmux call grew the flag."""
    sh = AGENT_TERMINALS_SH.read_text()
    assert "new-session -A -d" in sh, "the session-recreate helper moved; re-check the regex"
    assert _spawn_tmux_invocation().count("new-session") == 1
