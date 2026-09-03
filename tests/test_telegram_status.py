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
# `·` bullets, and here one is the very last body line — directly above the chrome.
# It is INDENTED (Claude gutter-indents its own output; a real status line is at
# column 0), and it is not a status. A spinner grep would relay "third bullet".
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


def test_a_settled_line_found_only_by_scanning_past_a_banner_is_rejected():
    # ⭐ Widening the scan must not resurrect a settled/past-tense summary that is
    # only reachable by skipping a non-spinner banner row: the ellipsis check gates
    # every row after the first non-blank one, exactly as it does for a stale
    # spinner line further up the pane (the test above).
    pane = TIP_UPDATE_BANNER_PANE.replace(
        "✽ Wandering… (2m 10s · ↓ 6.2k tokens)", "✻ Worked for 1m 17s"
    )
    assert detect_status(pane) is None


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
