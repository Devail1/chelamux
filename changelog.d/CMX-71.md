### Changed

- **Docs: the README, `docs/` and the landing site describe 0.15.** The README opens with
  what chela is, one install command (`uv sync --all-extras`) and a first run, then a
  "What's new in 0.15" list. Two new references: `docs/RUNS.md` (a dispatched run from claim
  to merge — the judge, risk caps, `retry` / `reopen` / `close --requeue`, and the one-tap
  override whose approval completes the merge) and `docs/DASHBOARD.md` (the sidebar's folder
  groups, VIEW menu, orchestrator badge and Archive-closes / Unarchive-resumes, window naming
  and Remote Control, the Linear Work board, the Usage view). `docs/GETTING_STARTED.md` has
  one install path and no longer says the agent strikes its own `TODO.md` line or opens its
  own PR. `docs/LINEAR_TRACKER.md` covers the state transitions and the Work board, and
  `docs/RISK_LEVELS.md` the `risk:` label on a Linear issue. The landing pages drop stale
  claims (a "full access" share — shares are view-only unless a sandboxed session allows
  typing; an override that waits; a rework cap of 2). (CMX-71)
- **Docs: `docs/TRUST_AND_APPROVAL_POSTURE.md` §2 no longer says per-agent sandboxing "does
  not exist yet".** The sandbox boundary (`chela/sandbox.py`) is implemented and measured,
  opt-in per workflow (`agent.sandbox: true`) for the coding and rework roles only, and off
  for chelamux's own dispatch, so its agents still run unsandboxed. (CMX-71, CMX-47)
- **Docs: spikes, upstream requests and the dated Symphony conformance review moved to
  `docs/archive/`**, with every reference in code comments, tests and docs updated. (CMX-71)
