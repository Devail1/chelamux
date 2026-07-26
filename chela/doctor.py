"""``chela doctor`` — for every fact chela depends on: what is REALLY in force?

The failure this exists to catch is not a crash. It is silence. Three examples, all from
one week:

* ``ecosystem.config.js`` carried ``CHELA_TMUX_SESSION: 'ccbot'`` in three ``env:``
  blocks for a day after the tmux session was renamed to ``chela``. Nothing complained —
  the live processes had the right value in their environment already, and only a clean
  ``pm2 start`` would have brought the fleet up against a session that no longer exists.
* The dashboard bound port 5005 from a ``--port`` flag, so the port lived *inside that
  one process*. ``chela plugin``, a different process, rendered the hooks manifest
  against the default 5001. Every hook then POSTed into a closed socket and failed open,
  exactly as designed — so the entire hook feature did nothing, and said nothing.
* The dispatcher was dead for nine hours and doctor printed ALL-GREEN: the running
  environment and the env file **agreed — and both were wrong**. Checking that two copies
  of a fact match is not checking the fact.

**This module is no longer a list of checks.** It was seven of them
(``_check_env_file``, ``_check_drift``, ``_check_session``, ``_check_dashboard_port``,
``_check_plugin``, ``_check_daemon``, ``_check_hold``), and every hand-written check
acquired its own private blind spot: CMX-63 shipped a drift check that compared only the
FIRST hook of the first entry, so a stale ``SessionStart`` read green. CMX-65 shipped a
test wrapper naming ONE ``.mjs`` file, so three suites — one of them red — ran nowhere.

So doctor is now GENERATED from :mod:`chela.runtime_truth`, the registry of every fact
chela's behaviour depends on: for each fact, read the value chela DECLARES, read back the
value its OWNER really has, compare, report. A new fact is checked by the act of being
registered; there is no eighth check to forget to write, and no private blind spot to
acquire. An owner that cannot be read is reported LOUDLY — never as a silent pass.

:data:`ERROR` findings mean something is broken right now; the CLI exits 1.

**CMX-187: a red finding used to reach only whoever happened to run ``chela doctor``.**
On 2026-07-26, ``relay.transcripts`` diagnosed a dead outbound relay for ``@78``
perfectly — ERROR, with the cause spelled out — and it sat unseen for hours: nobody was
running doctor, so nobody ever read it. :func:`check_and_notify` closes that gap the same
way :func:`chela.notify.check_waiting` and :func:`chela.update.check_and_notify` already
close it for their own facts: called from the daemon loop on a bounded cadence, it pushes
every ERROR finding through :mod:`chela.notify` on the transition into red, edge-triggered
so a fact that stays broken is announced once, not every tick. Because it runs the same
:func:`audit_all` the CLI does, a new registry fact is covered by the act of being
registered — there is no second place to wire a push for it.
"""
from __future__ import annotations

import logging

from chela import notify
from chela.runtime_truth import (  # noqa: F401 — doctor's public surface, re-exported
    ERROR,
    KNOWN_VARS,
    OK,
    WARN,
    Fact,
    Finding,
    audit,
    audit_all,
    fact,
    facts,
    installed_hooks_stale,
)

log = logging.getLogger(__name__)


def check() -> list[Finding]:
    """Every fact in the registry, in the order a human wants to read them."""
    return audit_all()


def check_and_notify(previously_red: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Called from the daemon loop on a bounded cadence. Edge-triggered exactly like
    :func:`chela.notify.check_waiting` / :func:`chela.update.check_and_notify`: an ERROR
    finding is logged and (if configured) pushed once on the transition into red — never
    once per tick, because a drumbeat of the same line is how an operator learns to ignore
    the log.

    Findings are identified by ``(fact, title)`` rather than by fact name alone, so a
    *second*, distinct ERROR under an already-red fact (a different stuck PR, a different
    dead window) still gets its own notification instead of hiding behind the first.

    Returns the current red set, to be passed back in as the next call's
    ``previously_red``.
    """
    findings = check()
    current_red = {(f.fact, f.title) for f in findings if f.level == ERROR}
    newly_red = current_red - previously_red
    if not newly_red:
        return current_red
    for fact_name, title in sorted(newly_red):
        log.error("doctor: %s: %s", fact_name, title)
    if notify.enabled():
        message = "\n".join(f"✗ {title}" for _, title in sorted(newly_red))
        notify.send(message, title="chela doctor: new red finding(s)")
    return current_red
