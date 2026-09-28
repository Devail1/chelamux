## 387. A fail-closed refusal chain is fixtured only on the default configuration, so its config-shaped arms fall through to a gate the fixture already passes

**Assertion form:** a gate is a chain of "cannot tell ⇒ refuse" arms, some about the
*input* (PR state, a struck line, an unreadable file) and some about the *configuration*
the input lives in (the config cannot be loaded at all; the configured backend kind has no
notion of the fact being checked). Every fixture builds one helper's default config — a
loadable workflow of the default tracker kind — and varies only the input. The input arms
are each armed and asserted; the config arms are never reached, because no fixture ever
changes the config.

**Mutation that defeats it:** fold a config arm into a pass —
`return ("…has no struck-line…")` → `return None if True else (…)`. Nothing reaches the
arm, so the suite stays green. Worse, it is not a crash waiting to happen: the refusal was
fail-closed, so removing it makes the chain FALL THROUGH to the next gate — and the
fixtures that would reach it are exactly the ones set up to pass that gate (a moved head),
so the real-world outcome is a silent approval on an unsupported config.

**Guard form that survives:** for each config arm, mount the non-default config (a
different backend kind; a missing file; an unknown kind that fails to load) with every
*other* gate in the chain set to PASS, and assert the refusal AND the arm's own reason
string. Passing the downstream gates is what makes the test a guard: if the arm is removed,
the result flips to `ok: True`, not merely to a different refusal.

**Found:** CMX-387 rework round 1 (2026-09-29), PR #539 — `chela/dispatcher.py`'s
`_done_reopen_refusal` refuses a `done` reopen when the tracker cannot be loaded or its
kind (gh_issues) has no struck line. Every test used a markdown `WORKFLOW.md`, so the judge
nulled both arms and 4222 tests stayed green. Closed by a gh_issues-workflow test and a
missing/unknown-kind workflow test, each with a moved head so a missing arm reopens the run.
