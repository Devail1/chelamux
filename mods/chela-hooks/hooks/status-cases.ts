// Shared by status.test.ts and pytest (tests/test_mods_status.py). `usage` is what
// `$.session.usage()` answers; `payload` is the statusLine-shaped body the mod pipes to
// scripts/cache-statusline.sh for it. Plain JSON: pytest parses it after the `=`.
export const STATUS = {
  "session_id": "0f0f0f0f-0000-4000-8000-000000000001", "cwd": "/repo", "model": "claude-opus-5-5",
  "usage": {
    "startedAt": 0,
    "context": {"tokens": 84000, "window": 200000, "percent": 42},
    "rateLimits": [{"kind": "five_hour", "percentUsed": 23.5, "resetsAt": "2026-10-17T12:00:00Z"},
                   {"kind": "seven_day", "percentUsed": 7, "resetsAt": "2026-10-20T00:00:00Z"}],
    "cost": {"usd": 1.25}
  },
  "payload": {
    "session_id": "0f0f0f0f-0000-4000-8000-000000000001", "cwd": "/repo", "workspace": {"current_dir": "/repo"},
    "model": {"id": "claude-opus-5-5", "display_name": "claude-opus-5-5"},
    "context_window": {"context_window_size": 200000, "total_input_tokens": 84000,
                       "used_percentage": 42, "remaining_percentage": 58},
    "rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": 1792238400},
                    "seven_day": {"used_percentage": 7, "resets_at": 1792454400}},
    "cost": {"total_cost_usd": 1.25}
  }
} as const
