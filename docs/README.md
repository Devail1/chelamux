# docs/

The user-facing documentation lives on the site — **[chela.pages.dev/docs](https://chela.pages.dev/docs)**.
This directory holds the long-form references the site links out to, plus the design records
behind decisions that are otherwise invisible in the code.

## References — read these

| File | What it covers |
|---|---|
| [GETTING_STARTED.md](GETTING_STARTED.md) | ~10 minutes, clone to your first dispatched agent |
| [RUNS.md](RUNS.md) | A dispatched run from claim to merge: the judge, risk caps, `retry` / `reopen` / `close --requeue`, and the override approval |
| [DASHBOARD.md](DASHBOARD.md) | The Wall, the sidebar (folder groups, VIEW menu, Archive), window naming + Remote Control, the Work board, the Usage view |
| [LINEAR_TRACKER.md](LINEAR_TRACKER.md) | Dispatching from a Linear team (`tracker: kind: linear`): setup, the API key, what chela reads |
| [RISK_LEVELS.md](RISK_LEVELS.md) | `high` / `normal` / `low` tasks: what the level changes (judge search, rework cap) and what it never does |
| [CONFIG.md](CONFIG.md) | Where configuration lives (`chela.env`), precedence, and PM2 |
| [HOOKS.md](HOOKS.md) | The Claude Code hooks plugin — what it enables and what it costs |
| [MODS_SPIKE.md](MODS_SPIKE.md) | Spike: the hook layer as a Claude Code Mod, measured side by side, and the go/no-go |
| [EVENTS.md](EVENTS.md) | The event log: kinds, shape, rotation |
| [TRUST_AND_APPROVAL_POSTURE.md](TRUST_AND_APPROVAL_POSTURE.md) | Who may dispatch, what a running agent may do, what approves a merge — in one place |
| [ESCALATION_CONTRACT.md](ESCALATION_CONTRACT.md) | When an agent must stop and surface to a human |
| [SANDBOX_BOUNDARY.md](SANDBOX_BOUNDARY.md) | The opt-in sandbox for dispatched coding agents: what was measured, and why it is off for chelamux |
| [SHARE_SANDBOX.md](SHARE_SANDBOX.md) | Sandboxed share sessions — letting a guest type, web mode, access requests |
| [RESOURCE_ISOLATION.md](RESOURCE_ISOLATION.md) | The known gap: nothing bounds what an agent *consumes* |
| [AGENT_IDENTITY.md](AGENT_IDENTITY.md) | How a window, a session and an agent are told apart |
| [JUDGE_EVAL.md](JUDGE_EVAL.md) | `chela judge eval` — an offline eval of the judge's experiment design |
| [DEFEAT_SHAPES.md](DEFEAT_SHAPES.md) + [defeat_shapes/](defeat_shapes/) | The catalogue of ways a guard passes while proving nothing — one file per shape |
| [OKF.md](OKF.md) | Open Knowledge Format export design |
| [ORCHESTRATOR_PERSONA.md](ORCHESTRATOR_PERSONA.md) · [PERSONA_PATTERN.md](PERSONA_PATTERN.md) | Giving an agent a stable standing role |
| [FRONTEND_PORT_PLAN.md](FRONTEND_PORT_PLAN.md) | Porting the whole dashboard to React + TypeScript: inventory, target architecture, the ordered port slices |
| [TEST_ARCHITECTURE_PLAN.md](TEST_ARCHITECTURE_PLAN.md) | The test suite's flakes, hermeticity and speed: what was measured, two tiers, fakes, a leak guard, the ordered slices |

## Design records — context, not instructions

These describe *why* something is the way it is. They are not kept current with the code.

- [wall-redesign.md](wall-redesign.md) — the wall's layout model
- [COLLAB_UX_SCOPE.md](COLLAB_UX_SCOPE.md) — what collaborative terminals deliberately do not do
- [SETTINGS_UI_INVENTORY.md](SETTINGS_UI_INVENTORY.md) — every settings knob and where it surfaces
- [perf/2026-10-profile.md](perf/2026-10-profile.md) — a profile of chela's services under normal load, and its harness

## Archive

[archive/](archive/) holds spikes, upstream requests and dated conformance reviews. They are
point-in-time records, kept because source comments and tests cite them:

- [SPIKE_LIVE_TERMINAL_TIMESTAMPS.md](archive/SPIKE_LIVE_TERMINAL_TIMESTAMPS.md) · [SPIKE_WALL_FILLS_STAGE.md](archive/SPIKE_WALL_FILLS_STAGE.md) — spikes
- [UPSTREAM_REQUEST_tui_timestamps.md](archive/UPSTREAM_REQUEST_tui_timestamps.md) — a request filed upstream
- [SYMPHONY_CONFORMANCE_2026-09-13.md](archive/SYMPHONY_CONFORMANCE_2026-09-13.md) — a conformance review against the Symphony spec
