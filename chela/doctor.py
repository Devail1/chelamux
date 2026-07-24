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
"""
from __future__ import annotations

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


def check() -> list[Finding]:
    """Every fact in the registry, in the order a human wants to read them."""
    return audit_all()
