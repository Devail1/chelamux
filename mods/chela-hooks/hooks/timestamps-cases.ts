// Shared by timestamps.test.ts and pytest (tests/test_mods_timestamps.py). `handshake` is
// what the mod adds to the `classic.MessageDisplay` payload the plugin/ hook pipes to the
// daemon while the Mod draws the timestamp band; the daemon answers `{}` for it, so the
// reply is never stamped twice. `body` is a first-batch MessageDisplay payload.
// Plain JSON: pytest parses it after the `=`.
export const TIMESTAMPS = {
  "handshake": {"chela_timestamp_owner": "mod"},
  "body": {"hook_event_name": "MessageDisplay", "session_id": "s-1", "transcript_path": "",
           "cwd": "/repo", "turn_id": "t-1", "message_id": "m-1", "index": 0,
           "final": false, "delta": "Done.\n"}
} as const
