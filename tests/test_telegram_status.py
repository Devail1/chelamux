"""The ephemeral status line — the live "Claude is working" verb, self-deleting.

Locks in the two halves of CMX-43:

  * :func:`detect_status` reads the working verb (and the background-shell count)
    off a pane by **anchoring on the chrome separator**, not by grepping for a
    spinner glyph. Claude's own prose is full of ``·`` bullets, so a grep would
    relay one; the panes below are real captures (Claude Code 2.1.207) and include
    that exact false positive as a test.
  * :class:`StatusRelay` posts ONE message per window, edits it in place while the
    verb changes (throttled, de-duped), and stops ticking when the turn ends —
    poofing it, or settling it, per :func:`should_keep`.

**The trap, and why "delete when the line leaves the pane" is not enough:** a
finished turn does not clear the status slot, it rewrites it in the past tense
behind the same spinner glyph (``✻ Worked for 1m 17s · 1 shell still running``,
``✻ Churned for 2m 31s``) — and leaves it there. A parser that just looks for a
spinner sees a live agent forever, and the "ephemeral" message never poofs.
"""
from __future__ import annotations

import pytest

from chela.telegram.gatewatch import (
    PermissionGateWatcher,
    STATUS_EDIT_MIN_INTERVAL,
    STATUS_KEEP_MIN_SECONDS,
    StatusRelay,
    format_status_message,
    should_keep,
)
from chela.telegram.panescan import Status, detect_status

_RULE = "─" * 40

# A real working pane: the verb sits at column 0 directly above the chrome, and the
# shell count is NOT in it — it is a segment of the mode line below the prompt box.
WORKING_PANE = f"""\
● Running 1 shell command…
  ⎿  $ tmux capture-pane -t chela:@32 -p

· Cerebrating… (1m 38s · ↓ 4.6k tokens)

{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on · 2 shells · ← for agents
"""

# The SETTLED pane: the turn is over, but the line persists, wearing the same glyph
# in the same slot. Its shells ("still running") outlived the turn.
SETTLED_SHELLS_PANE = f"""\
● Done.

✻ Worked for 1m 17s · 1 shell still running

{_RULE}
❯ fix the OI metric name too
{_RULE}

  ⏵⏵ auto mode on · 1 shell · ← for agents
"""

# A settled turn with nothing left running.
SETTLED_QUIET_PANE = f"""\
✻ Churned for 2m 31s

{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents
"""

# An idle pane: the line above the chrome is transcript tail, not a status.
IDLE_PANE = f"""\
  ⎿  Tip: Dynamic workflows let Claude write a script that orchestrates agents.
     keyword ultracode or ask Claude to use a workflow directly.

{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents
"""

# ⛔ THE FALSE POSITIVE the anchoring exists to prevent: Claude's output is full of
# `·` bullets, and here one is the second-to-last body line (a blank spacer sits
# between the last bullet and the chrome — see BULLET_DIRECTLY_ABOVE_CHROME_PANE
# below for the row truly adjacent to the chrome). It is INDENTED (Claude
# gutter-indents its own output; a real status line is at column 0), and it is not
# a status. A spinner grep would relay "third bullet".
BULLETS_PANE = f"""\
● Here is the plan:
  · rotate the group
  · filter-repo, then ask GitHub to purge
  · third bullet

{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on · ← for agents
"""

# ⛔ Same false positive as BULLETS_PANE, but with the blank spacer removed so the
# indented bullet sits at chrome_idx-1 — the row immediately above the chrome, and
# the ONLY row that takes the `is_first` path (which accepts active-or-settled
# unconditionally once column-0 passes). No other fixture in this file puts an
# indented "· " bullet directly there, so this is the one position where a relaxed
# column-0 check costs the most: it would relay "third bullet" as a live status.
BULLET_DIRECTLY_ABOVE_CHROME_PANE = f"""\
● Here is the plan:
  · rotate the group
  · filter-repo, then ask GitHub to purge
  · third bullet
{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on · ← for agents
"""

# ⛔ Same false positive as BULLETS_PANE, but positioned to isolate the column-0 rule
# from the ellipsis rule instead of relying on both at once. BULLETS_PANE's bullets
# carry no "…", so the ellipsis gate alone already rejects them at every row past the
# first — a mutation that drops the column-0 check for non-first rows would still pass
# BULLETS_PANE. Here the indented bullet DOES carry "…" and sits behind a banner row
# (not the first non-blank row above the chrome), so the ellipsis gate alone would
# accept it; only the column-0 check rejects it. detect_status must still return None.
INDENTED_ELLIPSIS_PANE = f"""\
● Here is the plan:
  ⎿  Tip: some tip text here
  · deploying the thing…
✔ Update installed · Restart to update
{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on · ← for agents
"""

# ⛔ Same false positive as INDENTED_ELLIPSIS_PANE, pushed one row further back: the
# ellipsis-carrying indented bullet now sits at chrome_idx-3, not chrome_idx-2. The
# widened scan reaches chrome_idx-1..chrome_idx-4 (_STATUS_LOOKBACK == 4), so this
# row is still in bounds — but until this fixture existed, only chrome_idx-2 had
# column-0 coverage, so a mutation that kept the column-0 rule for the two rows
# nearest the chrome and dropped it for the rest still passed every fixture here.
# detect_status must still return None.
INDENTED_ELLIPSIS_DEEPER_PANE = f"""\
● Here is the plan:
  · deploying the thing…
  ⎿  Tip: some tip text here
✔ Update installed · Restart to update
{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on · ← for agents
"""

# Same shape again, but at chrome_idx-4 — the FARTHEST row the widened scan reaches.
# Pins the column-0 rule at the far end of the lookback, not just the near end.
INDENTED_ELLIPSIS_DEEPEST_PANE = f"""\
  · deploying the thing…
  ⎿  Tip: some tip text here
     continued
✔ Update installed · Restart to update
{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on · ← for agents
"""

# ⛔ A settled/past-tense line reachable ONLY by scanning past a banner, pinned at
# chrome_idx-2 — the NEAR end of the widened scan. The two existing fixtures that
# exercise the ellipsis gate on a non-first row (below) both happen to put the
# settled line at chrome_idx-4, the FAR end, so a mutation that enforces the
# ellipsis gate only near the chrome (e.g. `i > chrome_idx - 4`) and accepts a
# settled column-0 line unconditionally at every nearer row would still pass every
# other fixture here. detect_status must still return None.
SETTLED_BEHIND_BANNER_NEAR_PANE = f"""\
● Running 1 shell command…
  ⎿  $ tmux capture-pane -t chela:@32 -p

✻ Worked for 1m 17s
✔ Update installed · Restart to update
{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on · 2 shells · ← for agents
"""

# Same shape, one row further back: the settled line sits at chrome_idx-3, behind
# two banner rows instead of one. Together with SETTLED_BEHIND_BANNER_NEAR_PANE
# (chrome_idx-2) and the existing chrome_idx-4 fixtures below, this pins the
# ellipsis gate at every row the widened scan reaches.
SETTLED_BEHIND_TWO_BANNERS_PANE = f"""\
● Running 1 shell command…
✻ Worked for 1m 17s
  ⎿  Tip: some tip text here
✔ Update installed · Restart to update
{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on · 2 shells · ← for agents
"""

# No chrome at all (scrolled back, or not a Claude window) — we cannot say anything.
NO_CHROME_PANE = """\
$ ls -la
total 12
· Cerebrating… (1m 38s · ↓ 4.6k tokens)
"""

# ── issue #432: a tip block + update banner between the status line and chrome ──
#
# Claude Code 2.1.259 can draw a tip block *and* an update banner between the live
# status line and the chrome rule — measured on a real 36-row pane: status at row
# 26, the tip block at 27-28, the update banner at 29, chrome at 30 (a gap of
# EXACTLY 4, i.e. _STATUS_LOOKBACK — see TIP_UPDATE_ONE_TOO_FAR_PANE below for the
# gap-of-5 boundary case). The old `detect_status` took the first non-blank row
# above the chrome and broke unconditionally, so it read the update banner instead
# of ever reaching the status line — silently, since a missing status line looks
# exactly like an idle pane.
TIP_UPDATE_BANNER_PANE = f"""\
● Running 1 shell command…
  ⎿  $ tmux capture-pane -t chela:@32 -p

✽ Wandering… (2m 10s · ↓ 6.2k tokens)
  ⎿  Tip: Running multiple Claude sessions in parallel can help you move faster
     for complex tasks
✔ Update installed · Restart to update
{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on · 2 shells · ← for agents
"""

# Same shape, but the status line sits ONE row further back (gap of 5, one past
# _STATUS_LOOKBACK) — must still resolve to None. Pins the boundary as "<=
# _STATUS_LOOKBACK reaches, one more does not", not merely "somewhere in range".
TIP_UPDATE_ONE_TOO_FAR_PANE = f"""\
● Running 1 shell command…
  ⎿  $ tmux capture-pane -t chela:@32 -p

✽ Wandering… (2m 10s · ↓ 6.2k tokens)

  ⎿  Tip: Running multiple Claude sessions in parallel can help you move faster
     for complex tasks
✔ Update installed · Restart to update
{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on · 2 shells · ← for agents
"""

# ⭐ The ACCEPTANCE half of the widened scan, closed at the NEAR end: TIP_UPDATE_BANNER_PANE
# above only proves "found behind a tip block and/or an update banner" at chrome_idx-4 (a
# TWO-line tip block). A tip that fits on one line is the same feature rendering shorter
# text, and it puts the status at chrome_idx-3 — a depth with no positive coverage at all.
# detect_status MUST still find the status line here (must NOT be None).
TIP_UPDATE_ONE_LINE_TIP_PANE = f"""\
● Running 1 shell command…
  ⎿  $ tmux capture-pane -t chela:@32 -p

✽ Wandering… (2m 10s · ↓ 6.2k tokens)
  ⎿  Tip: some short tip
✔ Update installed · Restart to update
{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on · 2 shells · ← for agents
"""

# Same acceptance shape, one row nearer still: no tip block at all, just the update banner
# between the status line and the chrome — status at chrome_idx-2. Closes the near end of
# the widened scan's ACCEPTANCE path entirely (chrome_idx-2 through chrome_idx-4 above).
# detect_status MUST still find the status line here (must NOT be None).
UPDATE_BANNER_ONLY_PANE = f"""\
● Running 1 shell command…
  ⎿  $ tmux capture-pane -t chela:@32 -p

✽ Wandering… (2m 10s · ↓ 6.2k tokens)
✔ Update installed · Restart to update
{_RULE}
❯
{_RULE}

  ⏵⏵ auto mode on · 2 shells · ← for agents
"""

# ⭐ The OTHER acceptance path, closed at its only untested position: the production comment
# claims "the FIRST non-blank row is still accepted unconditionally (active or settled)",
# but every fixture that exercises that path (WORKING_PANE, SETTLED_SHELLS_PANE,
# SETTLED_QUIET_PANE) puts its status at chrome_idx-2, behind a blank spacer. No fixture
# puts a column-0 status line at chrome_idx-1 — the row BULLET_DIRECTLY_ABOVE_CHROME_PANE
# proves for the REJECTION direction only. A settled summary can sit with no blank spacer
# directly above the chrome (the same shape as SETTLED_SHELLS_PANE with the spacer removed).
# detect_status MUST still find the status line here (must NOT be None).
SETTLED_DIRECTLY_ABOVE_CHROME_PANE = f"""\
● Done.

✻ Worked for 1m 17s · 1 shell still running
{_RULE}
❯ fix the OI metric name too
{_RULE}

  ⏵⏵ auto mode on · 1 shell · ← for agents
"""


# ── the parser ──────────────────────────────────────────────────────────────


def test_working_pane_yields_verb_and_shells_from_the_mode_line():
    st = detect_status(WORKING_PANE)
    assert st is not None
    assert st.active is True
    assert st.verb == "Cerebrating… (1m 38s · ↓ 4.6k tokens)"
    assert st.shells == 2  # from the mode line — it is NOT in the status line


def test_every_spinner_frame_is_recognised():
    # Claude animates the glyph several times a second; each frame must parse.
    for glyph in "·✻✽✶✳✢":
        pane = WORKING_PANE.replace("· Cerebrating…", f"{glyph} Cerebrating…", 1)
        st = detect_status(pane)
        assert st is not None and st.active, glyph


def test_idle_pane_has_no_status():
    assert detect_status(IDLE_PANE) is None


def test_bullets_in_output_are_not_a_status_line():
    # The whole reason for anchoring on the chrome (and demanding column 0).
    assert detect_status(BULLETS_PANE) is None


def test_a_bullet_directly_above_the_chrome_is_not_a_status_line():
    # The row immediately above the chrome (chrome_idx-1) is the ONLY row that
    # takes the `is_first` path (active-or-settled accepted unconditionally once
    # column-0 passes) — so it is the one position where a relaxed column-0 check
    # costs the most. No other fixture puts an indented bullet exactly there.
    assert detect_status(BULLET_DIRECTLY_ABOVE_CHROME_PANE) is None


def test_an_indented_ellipsis_bullet_past_the_first_row_is_not_a_status_line():
    # The column-0 rule must hold on its own, not just alongside the ellipsis rule:
    # this bullet is indented (body text) but DOES carry "…", so only column-0 rejects
    # it. A guard that drops column-0 for non-first rows would wrongly accept it.
    assert detect_status(INDENTED_ELLIPSIS_PANE) is None


def test_an_indented_ellipsis_bullet_at_chrome_minus_3_is_not_a_status_line():
    # Column-0 must hold three rows back too, not just at the nearest indented row
    # (chrome_idx-2, pinned above). A mutation that enforces column-0 only for the
    # two rows nearest the chrome and relaxes it deeper would wrongly accept this.
    assert detect_status(INDENTED_ELLIPSIS_DEEPER_PANE) is None


def test_an_indented_ellipsis_bullet_at_the_farthest_lookback_row_is_not_a_status_line():
    # Same shape at chrome_idx-4 — the farthest row _STATUS_LOOKBACK reaches. Pins
    # the column-0 rule at the far end of the widened scan, not just the near end.
    assert detect_status(INDENTED_ELLIPSIS_DEEPEST_PANE) is None


def test_pane_without_chrome_has_no_status():
    assert detect_status(NO_CHROME_PANE) is None


def test_empty_pane_has_no_status():
    assert detect_status("") is None


def test_settled_turn_is_not_active_and_keeps_its_shells():
    # THE TRAP: same glyph, same slot, but the turn is OVER — and this line persists,
    # so it can never be the *absence* of a line that ends the relay.
    st = detect_status(SETTLED_SHELLS_PANE)
    assert st is not None
    assert st.active is False
    assert st.shells == 1
    assert st.seconds == 77  # 1m 17s


def test_settled_turn_with_nothing_running():
    st = detect_status(SETTLED_QUIET_PANE)
    assert st is not None
    assert st.active is False
    assert st.shells is None
    assert st.seconds == 151  # 2m 31s


def test_unknown_past_tense_verb_settles_rather_than_ticking_forever():
    # The verb family is open-ended ("Worked", "Churned", …) so the ellipsis is the
    # discriminator: an unrecognised shape must fail CLOSED (settled), never stick.
    pane = SETTLED_QUIET_PANE.replace("Churned for 2m 31s", "Meandered for 4s")
    st = detect_status(pane)
    assert st is not None and st.active is False


def test_a_stale_spinner_line_further_up_the_pane_is_ignored():
    # Only the line directly above the chrome counts; an old turn's summary sitting
    # in the scrollback must not be mistaken for the current state.
    pane = IDLE_PANE.replace("● Here", "x")  # no-op; keep IDLE shape
    pane = "✻ Churned for 2m 31s\n" + pane
    assert detect_status(pane) is None


def test_status_line_is_found_behind_a_tip_block_and_update_banner():
    # issue #432: the old unconditional break read the update banner (the first
    # non-blank row above the chrome) and returned None, so the feature silently
    # stopped firing. The real status line sits exactly _STATUS_LOOKBACK rows back.
    st = detect_status(TIP_UPDATE_BANNER_PANE)
    assert st is not None
    assert st.active is True
    assert st.verb == "Wandering… (2m 10s · ↓ 6.2k tokens)"
    assert st.shells == 2


def test_status_line_one_row_beyond_the_lookback_is_not_found():
    # Same shape as the fixture above, but the status line sits one row past
    # _STATUS_LOOKBACK — pins the boundary exactly, not just "somewhere in range".
    assert detect_status(TIP_UPDATE_ONE_TOO_FAR_PANE) is None


def test_status_line_is_found_behind_a_one_line_tip_and_update_banner():
    # The rejection gates were pinned at every reachable depth (rounds 2-3), but the
    # ACCEPTANCE half of the same scan was only ever proven at chrome_idx-4 (a two-line
    # tip block). A mutation that restricts the scan-past acceptance to `i == chrome_idx
    # - 4` exactly would silently return None here — issue #432 all over again, one row
    # nearer the chrome.
    st = detect_status(TIP_UPDATE_ONE_LINE_TIP_PANE)
    assert st is not None
    assert st.active is True
    assert st.verb == "Wandering… (2m 10s · ↓ 6.2k tokens)"
    assert st.shells == 2


def test_status_line_is_found_behind_an_update_banner_with_no_tip_block():
    # Same acceptance path, at the NEAREST depth the widened scan reaches: no tip block
    # at all, only the update banner between the status line and the chrome.
    st = detect_status(UPDATE_BANNER_ONLY_PANE)
    assert st is not None
    assert st.active is True
    assert st.verb == "Wandering… (2m 10s · ↓ 6.2k tokens)"
    assert st.shells == 2


def test_a_settled_status_directly_above_the_chrome_is_found():
    # The OTHER acceptance path: "the first non-blank row is accepted unconditionally"
    # was only ever proven at chrome_idx-2 (behind a blank spacer). A mutation that
    # restricts that unconditional accept to `i == chrome_idx - 2` exactly would
    # silently return None for a settled summary with no spacer, directly above the
    # chrome — losing the "shell still running" warning and the turn receipt.
    st = detect_status(SETTLED_DIRECTLY_ABOVE_CHROME_PANE)
    assert st is not None
    assert st.active is False
    assert st.shells == 1
    assert st.seconds == 77  # 1m 17s


def test_a_settled_line_found_only_by_scanning_past_a_banner_is_rejected():
    # ⭐ Widening the scan must not resurrect a settled/past-tense summary that is
    # only reachable by skipping a non-spinner banner row: the ellipsis check gates
    # every row after the first non-blank one, exactly as it does for a stale
    # spinner line further up the pane (the test above). This fixture puts the
    # settled line at chrome_idx-4, the FAR end of the widened scan — see the two
    # tests below for the NEAR end (chrome_idx-2, chrome_idx-3).
    pane = TIP_UPDATE_BANNER_PANE.replace(
        "✽ Wandering… (2m 10s · ↓ 6.2k tokens)", "✻ Worked for 1m 17s"
    )
    assert detect_status(pane) is None


def test_a_settled_line_two_rows_back_is_rejected():
    # Pins the ellipsis gate at chrome_idx-2 — the row nearest the chrome that is
    # still NOT the unconditional-accept first row. A mutation that enforces the
    # gate only near the far end of the lookback (e.g. only at chrome_idx-4) would
    # accept this settled line unconditionally instead.
    assert detect_status(SETTLED_BEHIND_BANNER_NEAR_PANE) is None


def test_a_settled_line_three_rows_back_is_rejected():
    # Same as above, one row further back (chrome_idx-3) — closes the remaining
    # reachable row between the chrome_idx-2 and chrome_idx-4 fixtures.
    assert detect_status(SETTLED_BEHIND_TWO_BANNERS_PANE) is None


# ── should_keep: the one place the keep-or-poof rule lives ───────────────────


def test_keep_when_shells_are_still_running():
    # Background work that outlived the turn is a WARNING, not a receipt — it is the
    # one genuinely actionable thing the status line ever says.
    st = Status(verb="Worked for 3s · 1 shell still running", shells=1,
                active=False, seconds=3)
    assert should_keep(st) is True


def test_keep_a_long_turn():
    st = Status(verb="Worked for 2m 31s", active=False, seconds=151)
    assert should_keep(st) is True


def test_keep_at_exactly_the_threshold():
    st = Status(verb="Worked for 30s", active=False, seconds=STATUS_KEEP_MIN_SECONDS)
    assert should_keep(st) is True


def test_poof_a_quick_turn_with_nothing_running():
    st = Status(verb="Worked for 6s", active=False, seconds=6)
    assert should_keep(st) is False


def test_poof_a_settled_turn_of_unknown_duration():
    st = Status(verb="Worked", active=False, seconds=None)
    assert should_keep(st) is False


# ── formatting ──────────────────────────────────────────────────────────────


def test_format_working_appends_the_shell_count():
    st = Status(verb="Cerebrating… (2m 45s · ↓ 12.0k tokens)", shells=2)
    assert format_status_message(st) == "✻ Cerebrating… (2m 45s · ↓ 12.0k tokens) · 2 shells"


def test_format_working_singular_shell():
    st = Status(verb="Cerebrating… (5s)", shells=1)
    assert format_status_message(st).endswith("· 1 shell")


def test_format_settled_does_not_repeat_the_shells_the_verb_already_names():
    st = Status(verb="Worked for 1m 17s · 1 shell still running", shells=1,
                active=False, seconds=77)
    assert format_status_message(st) == "✅ Worked for 1m 17s · 1 shell still running"


# ── the relay ───────────────────────────────────────────────────────────────


class FakeRegistry:
    def __init__(self, threads=None):
        self.threads = {"@1": "100"} if threads is None else threads

    def thread_for_window(self, wid):
        return self.threads.get(wid)


class FakeBot:
    """Records every Telegram call the status relay makes."""

    def __init__(self, *, post_id=7, edit_ok=True):
        self.posts: list[tuple] = []
        self.edits: list[tuple] = []
        self.deletes: list[int] = []
        self.typings: list = []
        self._post_id = post_id
        self._edit_ok = edit_ok

    def post(self, text, parse_mode=None, thread=None, markup=None):
        self.posts.append((text, thread))
        return self._post_id

    def edit(self, mid, text, parse_mode=None, markup=None):
        self.edits.append((mid, text))
        return self._edit_ok

    def delete(self, mid):
        self.deletes.append(mid)
        return True

    def typing(self, thread):
        self.typings.append(thread)
        return True


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _relay(bot, clock, registry=None):
    return StatusRelay(
        registry or FakeRegistry(),
        post=bot.post,
        edit=bot.edit,
        delete=bot.delete,
        typing=bot.typing,
        now=clock,
    )


def test_first_sight_posts_the_status_and_starts_typing():
    bot, clock = FakeBot(), Clock()
    _relay(bot, clock).sync("@1", WORKING_PANE)
    assert len(bot.posts) == 1
    text, thread = bot.posts[0]
    assert text.startswith("✻ Cerebrating…")
    assert thread == "100"
    assert bot.typings == ["100"]  # what makes a phone feel live


def test_an_unbound_window_posts_nothing():
    bot, clock = FakeBot(), Clock()
    _relay(bot, clock, FakeRegistry(threads={})).sync("@1", WORKING_PANE)
    assert bot.posts == []


def test_the_edit_is_throttled():
    # Claude repaints the line ~1/s; unthrottled, one working agent would walk the
    # topic straight into Telegram's flood limit.
    bot, clock = FakeBot(), Clock()
    relay = _relay(bot, clock)
    relay.sync("@1", WORKING_PANE)
    moved = WORKING_PANE.replace("1m 38s", "1m 40s")
    clock.t += STATUS_EDIT_MIN_INTERVAL / 2
    relay.sync("@1", moved)
    assert bot.edits == []  # inside the throttle window → dropped, not queued

    clock.t += STATUS_EDIT_MIN_INTERVAL
    relay.sync("@1", moved)
    assert len(bot.edits) == 1
    assert "1m 40s" in bot.edits[0][1]


def test_unchanged_text_makes_no_api_call():
    bot, clock = FakeBot(), Clock()
    relay = _relay(bot, clock)
    relay.sync("@1", WORKING_PANE)
    clock.t += STATUS_EDIT_MIN_INTERVAL * 2
    relay.sync("@1", WORKING_PANE)  # identical pane → identical body
    assert bot.edits == []
    assert len(bot.posts) == 1


def test_typing_is_refreshed_even_when_the_text_has_not_changed():
    # Telegram expires the indicator after ~5s; a stalled verb would otherwise look
    # exactly like a dead agent, which is the bug this whole feature exists to fix.
    bot, clock = FakeBot(), Clock()
    relay = _relay(bot, clock)
    relay.sync("@1", WORKING_PANE)
    clock.t += STATUS_EDIT_MIN_INTERVAL * 2
    relay.sync("@1", WORKING_PANE)
    assert bot.typings == ["100", "100"]


def test_idle_pane_poofs_the_status_message():
    bot, clock = FakeBot(), Clock()
    relay = _relay(bot, clock)
    relay.sync("@1", WORKING_PANE)
    relay.sync("@1", IDLE_PANE)
    assert bot.deletes == [7]


def test_a_quick_settled_turn_poofs():
    bot, clock = FakeBot(), Clock()
    relay = _relay(bot, clock)
    relay.sync("@1", WORKING_PANE)
    quick = SETTLED_QUIET_PANE.replace("Churned for 2m 31s", "Churned for 6s")
    relay.sync("@1", quick)
    assert bot.deletes == [7]


def test_a_settled_turn_with_shells_still_running_is_KEPT_not_deleted():
    # The whole point of keeping: "1 shell still running" is a live warning that
    # background work outlived the turn. Deleting it throws away the only signal.
    bot, clock = FakeBot(), Clock()
    relay = _relay(bot, clock)
    relay.sync("@1", WORKING_PANE)
    relay.sync("@1", SETTLED_SHELLS_PANE)
    assert bot.deletes == []
    assert bot.edits[-1] == (7, "✅ Worked for 1m 17s · 1 shell still running")


def test_a_long_settled_turn_is_kept():
    bot, clock = FakeBot(), Clock()
    relay = _relay(bot, clock)
    relay.sync("@1", WORKING_PANE)
    relay.sync("@1", SETTLED_QUIET_PANE)  # 2m 31s
    assert bot.deletes == []
    assert bot.edits[-1] == (7, "✅ Churned for 2m 31s")


def test_a_kept_message_is_never_edited_again():
    # The ticking must stop unconditionally — a kept summary is not a live message.
    bot, clock = FakeBot(), Clock()
    relay = _relay(bot, clock)
    relay.sync("@1", WORKING_PANE)
    relay.sync("@1", SETTLED_SHELLS_PANE)
    settled_edits = len(bot.edits)
    clock.t += STATUS_EDIT_MIN_INTERVAL * 10
    relay.sync("@1", SETTLED_SHELLS_PANE)
    relay.sync("@1", SETTLED_SHELLS_PANE)
    assert len(bot.edits) == settled_edits
    assert bot.deletes == []


def test_a_turn_that_never_posted_a_status_settles_into_nothing():
    # Too short to be caught between two polls — we never post a message just to
    # announce that a turn we never showed has finished.
    bot, clock = FakeBot(), Clock()
    _relay(bot, clock).sync("@1", SETTLED_SHELLS_PANE)
    assert bot.posts == [] and bot.edits == [] and bot.deletes == []


def test_a_new_turn_posts_a_fresh_message_after_one_was_kept():
    bot, clock = FakeBot(), Clock()
    relay = _relay(bot, clock)
    relay.sync("@1", WORKING_PANE)
    relay.sync("@1", SETTLED_SHELLS_PANE)  # kept
    relay.sync("@1", WORKING_PANE)  # next turn
    assert len(bot.posts) == 2
    assert bot.deletes == []  # the kept summary is left alone


def test_a_window_that_vanishes_from_the_polled_set_is_poofed():
    # A dead / unbound window stops being polled, so its status would otherwise hang
    # in the topic forever — the exact silt this design exists to prevent.
    bot, clock = FakeBot(), Clock()
    relay = _relay(bot, clock)
    relay.sync("@1", WORKING_PANE)
    relay.retain(["@2"])
    assert bot.deletes == [7]


def test_a_window_that_moves_to_another_topic_reposts():
    bot, clock = FakeBot(), Clock()
    registry = FakeRegistry()
    relay = _relay(bot, clock, registry)
    relay.sync("@1", WORKING_PANE)
    registry.threads["@1"] = "999"  # rebound to a different topic
    clock.t += STATUS_EDIT_MIN_INTERVAL * 2
    relay.sync("@1", WORKING_PANE)
    assert bot.deletes == [7]  # the message in the old topic is poofed…
    assert [t for _, t in bot.posts] == ["100", "999"]  # …and reposted in the new


def test_a_failing_edit_drops_the_tracking_and_reposts():
    bot, clock = FakeBot(edit_ok=False), Clock()
    relay = _relay(bot, clock)
    relay.sync("@1", WORKING_PANE)
    clock.t += STATUS_EDIT_MIN_INTERVAL * 2
    relay.sync("@1", WORKING_PANE.replace("1m 38s", "1m 44s"))
    assert len(bot.edits) == 1  # tried…
    clock.t += STATUS_EDIT_MIN_INTERVAL * 2
    relay.sync("@1", WORKING_PANE.replace("1m 38s", "1m 48s"))
    assert len(bot.posts) == 2  # …failed, so a fresh message replaces it


def test_every_telegram_failure_is_swallowed():
    # This is decoration. It must never wedge the relay or delay a real message.
    def boom(*_a, **_k):
        raise RuntimeError("429 / message deleted / not modified")

    registry = FakeRegistry()
    relay = StatusRelay(
        registry, post=boom, edit=boom, delete=boom, typing=boom, now=Clock()
    )
    relay.sync("@1", WORKING_PANE)  # post raises
    relay.sync("@1", IDLE_PANE)  # delete raises
    relay.retain([])  # and again


def test_a_broken_detector_cannot_take_the_watcher_down():
    def boom(_pane):
        raise ValueError("regex blew up")

    relay = StatusRelay(
        FakeRegistry(), post=FakeBot().post, edit=FakeBot().edit,
        delete=FakeBot().delete, detect=boom,
    )
    relay.sync("@1", WORKING_PANE)


# ── it rides the gate watcher's existing capture ────────────────────────────


def test_the_status_rides_the_existing_pane_capture_with_no_extra_tmux_calls():
    # A separate poller would double the tmux load on the whole fleet for nothing:
    # the status is a FOURTH read of the same captured text.
    captures: list[str] = []

    def capture(wid):
        captures.append(wid)
        return WORKING_PANE

    bot, clock = FakeBot(), Clock()
    registry = FakeRegistry()
    watcher = PermissionGateWatcher(
        lambda *a, **k: True,
        registry,
        capture=capture,
        status=_relay(bot, clock, registry),
    )
    watcher.poll(["@1"])
    assert captures == ["@1"]  # ONE capture for all four detectors
    assert len(bot.posts) == 1


def test_the_status_is_off_when_no_relay_is_wired():
    # The three gate prompts must behave exactly as before when it is disabled.
    watcher = PermissionGateWatcher(
        lambda *a, **k: True, FakeRegistry(), capture=lambda _w: WORKING_PANE
    )
    watcher.poll(["@1"])
    watcher.forget("@1")


def _banner_pane(status_line: str) -> str:
    """The tip-block + update-banner pane of `TIP_UPDATE_BANNER_PANE`, with the status
    row swapped — so a test can vary the SPINNER GLYPH while holding the layout fixed."""
    return (
        "◍ Running 1 shell command…\n"
        "  ⎿  $ tmux capture-pane -t chela:@32 -p\n"
        "\n"
        f"{status_line}\n"
        "  ⎿  Tip: Running multiple Claude sessions in parallel can help you move faster\n"
        "     for complex tasks\n"
        "✔ Update installed · Restart to update\n"
        f"{_RULE}\n"
        "❯\n"
        f"{_RULE}\n"
        "\n"
        "  ⏵⏵ auto mode on · 2 shells · ↰ for agents\n"
    )


@pytest.mark.parametrize("glyph", list("·✻✽✶✳✢"))
def test_scan_past_a_banner_accepts_EVERY_spinner_frame(glyph):
    """🔴 Claude animates the spinner — the module's own comment records `·` → `✶` → `✽`
    → `✻` cycling several times a second — so which glyph a pane shows is pure timing.
    Every scan-past-a-banner fixture froze on `✽`, which makes a mutation that restricts
    the acceptance path to one glyph (`... and line[0] == "✽"`) invisible.

    In production that mutation would not fail cleanly: the status line would resolve on
    roughly one frame in six and vanish on the rest, so the Telegram message would
    FLICKER — appearing, self-deleting and reappearing — which is far harder to diagnose
    than the silent stop issue #432 describes. All six glyphs must behave identically.
    """
    st = detect_status(_banner_pane(f"{glyph} Wandering… (2m 10s · ↓ 6.2k tokens)"))

    assert st is not None, f"spinner frame {glyph!r} was not accepted behind the banner"
    assert st.active is True
    assert st.verb == "Wandering… (2m 10s · ↓ 6.2k tokens)"


@pytest.mark.parametrize("glyph", list("·✻✽✶✳✢"))
def test_scan_past_a_banner_REJECTS_a_settled_line_on_EVERY_spinner_frame(glyph):
    """⭐ The counterweight, at the same coverage. A finished turn's summary is frozen on
    whatever frame the turn happened to end on, so the ellipsis gate must reject it for
    all six glyphs too — every settled fixture froze on `✻`, which lets a mutation that
    exempts one glyph (`... or line[0] != "✻"`) through.

    Without this, the pair of tests above would be satisfied by a parser that accepts
    everything behind a banner, which is the sticky-message failure the ellipsis
    discriminator exists to prevent (a frozen "Worked for 1m 17s" that never poofs).
    """
    pane = _banner_pane(f"{glyph} Worked for 1m 17s · 1 shell still running")

    assert detect_status(pane) is None, (
        f"a settled, past-tense line on frame {glyph!r} was read as a live status behind "
        "the banner — the ellipsis gate must reject every frame, not just ✻"
    )


@pytest.mark.parametrize("gap", [1, 2, 3, 4])
def test_first_nonblank_row_is_accepted_at_EVERY_depth_the_lookback_reaches(gap):
    """The production comment says the FIRST non-blank row is accepted unconditionally —
    active or settled — and carries no distance qualifier. Pre-PR the code did exactly
    that at any depth `_STATUS_LOOKBACK` reaches; the widened scan must not have quietly
    narrowed it to the rows nearest the chrome.

    Every existing fixture puts that row at gap 1 or 2, so `is_first = not
    seen_first_nonblank and i >= chrome_idx - 2` is invisible to them. This drives all
    four reachable depths.

    What the narrowing would cost is not nothing: a settled line resolves to
    `Status(active=False)` carrying `shells` and `seconds` — the relay uses `active` to
    poof the ephemeral message, and the counts are half of what the phone shows. Under the
    mutation, gaps 3 and 4 collapse to `None` instead, losing both.
    """
    rows = (
        ["◍ output", ""]
        + ["✻ Worked for 1m 17s · 1 shell still running"]
        + [""] * (gap - 1)
        + [_RULE, "❯", _RULE, "", "  ⏵⏵ auto mode on · 2 shells · ↰ for agents"]
    )
    st = detect_status("\n".join(rows) + "\n")

    assert st is not None, (
        f"a settled first non-blank row at gap {gap} resolved to None — the unconditional "
        "first-row acceptance was narrowed to the rows nearest the chrome"
    )
    assert st.active is False          # settled: the relay poofs the message
    assert st.verb == "Worked for 1m 17s · 1 shell still running"
    assert st.seconds == 77            # ⭐ the payload that None would have thrown away


def test_a_blank_spacer_does_not_make_a_later_row_count_as_the_FIRST_one():
    """🔴 `seen_first_nonblank` is a latch: once any non-blank row has been seen, every
    row above it needs the ellipsis. A blank spacer must not reset that latch.

    Under `seen_first_nonblank = False` on the blank branch, the settled line below
    becomes "first" again and is accepted unconditionally — so a finished turn's
    past-tense summary is relayed as a live status and the ephemeral message never poofs,
    which is the sticky-message failure the whole allowlist exists to prevent.

    No existing fixture puts a BLANK row between the banner and the status line, so the
    latch's reset was invisible.
    """
    pane = "\n".join([
        "◍ output",
        "",
        "✻ Worked for 1m 17s · 1 shell still running",   # settled, above a blank spacer
        "",                                               # ⭐ the blank the latch must survive
        "✔ Update installed · Restart to update",         # the first non-blank above chrome
        _RULE, "❯", _RULE, "",
        "  ⏵⏵ auto mode on · 2 shells · ↰ for agents",
    ]) + "\n"

    assert detect_status(pane) is None, (
        "a settled line separated from the banner by a blank row was accepted as a live "
        "status — the first-non-blank latch was reset by the spacer"
    )


@pytest.mark.parametrize("glyph", list("·✻✽✶✳✢"))
@pytest.mark.parametrize("gap", [2, 3, 4])
def test_settled_line_is_rejected_on_every_frame_at_every_reachable_depth(glyph, gap):
    """⭐ Round 6 parametrized the settled-rejection over all six frames, but only inside
    `_banner_pane` — which pins the status row at ONE depth. So the frame axis and the
    depth axis were each covered alone and never together, leaving a mutation that exempts
    one frame AT one depth (`line[0] == "·" and i == chrome_idx - 2`) invisible to both.

    This is the product of the two axes: every frame, at every depth past the first
    non-blank row that the lookback reaches.
    """
    rows = (
        ["◍ output", ""]
        + [f"{glyph} Worked for 1m 17s · 1 shell still running"]
        + ["  ⎿  filler"] * (gap - 2)
        + ["✔ Update installed · Restart to update"]
        + [_RULE, "❯", _RULE, "", "  ⏵⏵ auto mode on · 2 shells · ↰ for agents"]
    )

    assert detect_status("\n".join(rows) + "\n") is None, (
        f"a settled line on frame {glyph!r} at depth {gap} was read as live — the ellipsis "
        "gate must hold across BOTH axes, not each one alone"
    )


def test_the_scan_stops_at_the_NEAREST_accepted_spinner_row():
    """The widened scan must take the row closest to the chrome and stop. Before this PR
    the loop broke unconditionally after one non-blank row, so ordering could not matter;
    widening it made ordering a real property that nothing pinned.

    Replace the `break` with `pass` and the loop keeps going, so a FARTHER spinner row
    overwrites the nearer one — the relay would show a stale verb and elapsed time from
    earlier in the scrollback while the current turn runs.
    """
    pane = "\n".join([
        "◍ output",
        "✽ Stale… (99m 0s · ↓ 1.0k tokens)",     # farther: must NOT win
        "  ⎿  Tip: something between them",
        "✽ Current… (2m 10s · ↓ 6.2k tokens)",   # nearest to the chrome: must win
        _RULE, "❯", _RULE, "",
        "  ⏵⏵ auto mode on · 2 shells · ↰ for agents",
    ]) + "\n"

    st = detect_status(pane)

    assert st is not None
    assert st.verb.startswith("Current…"), (
        f"the scan returned {st.verb!r} — a farther spinner row overwrote the nearest one, "
        "so the relay would show a stale verb from earlier in the scrollback"
    )
