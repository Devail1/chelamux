// Shared by needsyou.test.ts and pytest (tests/test_mods_needsyou.py). `sources` is what
// the daemon's three stores hold; `response` is exactly what GET /api/needs-you answers for
// them (pytest proves it on the real route), and what the mod's tests feed the band.
// `share_requests` are records as the share-requests store keeps them (keyed by id there).
// `now_ms` is 2026-10-19T12:34:00Z. `base` stands in for CHELA_DASHBOARD_URL.
// Plain JSON: pytest parses it after the `=`.
export const NEEDS_YOU = {
  "now_ms": 1792413240000,
  "base": "https://chela.example",
  "sources": {
    "overrides": [{"id": "ov-1", "label": "CMX-12", "created": 1792413000}],
    "share_requests": [
      {"id": "r-1", "status": "pending", "kind": "mount", "target": "/data", "access": "ro",
       "filed_at": 1792412040},
      {"id": "r-2", "status": "approved", "kind": "mount", "target": "/old", "access": "ro",
       "filed_at": 1792400000}
    ],
    "runs": [
      {"task_id": "CMX-40", "status": "needs_human", "ended_at": "2026-10-19T12:33:00+00:00",
       "started_at": "2026-10-19T11:00:00+00:00"},
      {"task_id": "CMX-41", "status": "awaiting_review", "ended_at": "2026-10-19T10:00:00+00:00",
       "started_at": "2026-10-19T09:00:00+00:00"}
    ]
  },
  "response": {"items": [
    {"kind": "access", "label": "mount /data (read-only)", "since": 1792412040, "url": "https://chela.example/"},
    {"kind": "approval", "label": "CMX-12", "since": 1792413000, "url": "https://chela.example/override/ov-1"},
    {"kind": "run", "label": "CMX-40", "since": 1792413180, "url": "https://chela.example/"}
  ]}
} as const
