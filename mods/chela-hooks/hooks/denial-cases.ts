// Shared by denial.test.ts and pytest (tests/test_mods_denial.py). `event` is a
// classic.PermissionDenied as Claude Code hands it to the Mod (and, with `hook_event_name`,
// to plugin/'s shell hook); `wid` is $CHELA_WID; `body` is exactly what the Mod POSTs to
// /api/inbox/denial for them (the reason collapsed to one line), and what pytest files.
// Plain JSON: pytest parses it after the `=`.
export const DENIAL = {
  "wid": "@41",
  "event": {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}, "tool_use_id": "toolu_01denied",
            "reason": "Deletes files\n  outside the task", "session_id": "s-1",
            "transcript_path": "/tmp/s-1.jsonl", "cwd": "/repo"},
  "body": {"wid": "@41", "session_id": "s-1", "tool_name": "Bash", "tool_input": {"command": "rm -rf build"},
           "tool_use_id": "toolu_01denied", "reason": "Deletes files outside the task"}
} as const
