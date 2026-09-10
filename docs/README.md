# docs/

The user-facing documentation lives on the site — **[chela.pages.dev/docs](https://chela.pages.dev/docs)**.
This directory holds the long-form references the site links out to, plus the design records
behind decisions that are otherwise invisible in the code.

## References — read these

| File | What it covers |
|---|---|
| [GETTING_STARTED.md](GETTING_STARTED.md) | ~10 minutes, clone to your first dispatched agent |
| [CONFIG.md](CONFIG.md) | Every environment variable, with defaults |
| [HOOKS.md](HOOKS.md) | The Claude Code hooks plugin — what it enables and what it costs |
| [EVENTS.md](EVENTS.md) | The event log: kinds, shape, rotation |
| [RESOURCE_ISOLATION.md](RESOURCE_ISOLATION.md) | The known gap: nothing bounds what an agent *consumes* |
| [AGENT_IDENTITY.md](AGENT_IDENTITY.md) | How a window, a session and an agent are told apart |
| [ESCALATION_CONTRACT.md](ESCALATION_CONTRACT.md) | When an agent must stop and surface to a human |
| [DEFEAT_SHAPES.md](DEFEAT_SHAPES.md) + [defeat_shapes/](defeat_shapes/) | The catalogue of ways a guard passes while proving nothing — one file per shape |
| [OKF.md](OKF.md) | Open Knowledge Format export design |
| [ORCHESTRATOR_PERSONA.md](ORCHESTRATOR_PERSONA.md) · [PERSONA_PATTERN.md](PERSONA_PATTERN.md) | Giving an agent a stable standing role |

## Design records — context, not instructions

These describe *why* something is the way it is, or a spike that informed it. They are not
kept current with the code, and several are referenced from source comments and tests, so
they stay where they are rather than moving.

- [wall-redesign.md](wall-redesign.md) — the wall's layout model
- [COLLAB_UX_SCOPE.md](COLLAB_UX_SCOPE.md) — what collaborative terminals deliberately do not do
- [SETTINGS_UI_INVENTORY.md](SETTINGS_UI_INVENTORY.md) — every settings knob and where it surfaces
- [SPIKE_LIVE_TERMINAL_TIMESTAMPS.md](SPIKE_LIVE_TERMINAL_TIMESTAMPS.md) · [SPIKE_WALL_FILLS_STAGE.md](SPIKE_WALL_FILLS_STAGE.md) — spikes
- [UPSTREAM_REQUEST_tui_timestamps.md](UPSTREAM_REQUEST_tui_timestamps.md) — a request filed upstream
