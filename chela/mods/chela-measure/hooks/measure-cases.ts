// Shared by measure.test.ts and pytest (tests/test_measure_mod.py). `event` is one
// `session.measure` input in the shape Claude Code 2.1.330 declares (SessionMeasureInput:
// docs/spikes/SESSION_MEASURE.md §1); `file` is what the mod writes for it. Plain JSON:
// pytest parses it after the `=`. The five-hour reset has 750 ms, so a resets_at that
// rounds instead of flooring shows; `spend_limit` rides along and chela ignores it.
export const MEASURE = {
  "session_id": "0f0f0f0f-0000-4000-8000-000000000477", "cwd": "/repo", "model": "claude-opus-5-5",
  "event": {
    "context": {"tokens": 84000, "window": 200000, "percent": 42},
    "rateLimits": [{"kind": "five_hour", "percentUsed": 23.5, "resetsAt": "2026-10-17T12:00:00.750Z"},
                   {"kind": "seven_day", "percentUsed": 7, "resetsAt": "2026-10-20T00:00:00Z"},
                   {"kind": "spend_limit", "percentUsed": 3}],
    "cost": {"usd": 1.25},
    "changed": ["context", "rateLimits", "cost"]
  },
  "file": {
    "source": "mod", "session_id": "0f0f0f0f-0000-4000-8000-000000000477", "cwd": "/repo",
    "workspace": {"current_dir": "/repo"},
    "model": {"id": "claude-opus-5-5", "display_name": "claude-opus-5-5"},
    "context_window": {"context_window_size": 200000, "total_input_tokens": 84000,
                       "used_percentage": 42, "remaining_percentage": 58},
    "rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": 1792238400},
                    "seven_day": {"used_percentage": 7, "resets_at": 1792454400},
                    "spend_limit": {"used_percentage": 3}},
    "cost": {"total_cost_usd": 1.25}
  }
} as const
