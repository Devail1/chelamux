// Shared by the mod's own tests (register.test.ts) and pytest (tests/test_mods_spike.py),
// which runs every `gate` case through the REAL mergegate.py and every `events` payload
// through `chela.hooks.ingest`. Keep the value plain JSON: pytest parses it after the `=`.
export const CASES = {
  "gate": [
    {"command": "gh pr merge 5 --squash", "deny": true},
    {"command": "cd x && gh pr merge 5", "deny": true},
    {"command": "gh api -X PUT repos/o/r/pulls/5/merge", "deny": true},
    {"command": "gh api graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'", "deny": true},
    {"command": "git push origin HEAD:dev", "deny": true},
    {"command": "git push origin main", "deny": true},
    {"command": "git push --all origin", "deny": true},
    {"command": "uv run chela merge-approve override-abc", "deny": true},
    {"command": "tmux kill-server", "deny": true},
    {"command": "gh pr view 5", "deny": false},
    {"command": "gh pr create --base dev --fill", "deny": false},
    {"command": "git push origin cmx-12", "deny": false},
    {"command": "cat > body.md <<'EOF'\ngh pr merge 5\nEOF\nls", "deny": false},
    {"command": "uv run chela merge cmx-12", "deny": false},
    {"command": "tmux -L chelatest-x kill-server", "deny": false}
  ],
  "events": [
    {"hook_event_name": "SessionStart", "source": "startup", "session_id": "s-1", "transcript_path": "/tmp/s-1.jsonl", "cwd": "/tmp"},
    {"hook_event_name": "SessionEnd", "reason": "clear", "session_id": "s-1", "transcript_path": "/tmp/s-1.jsonl", "cwd": "/tmp"},
    {"hook_event_name": "Stop", "stop_hook_active": false, "last_assistant_message": "done", "session_id": "s-1", "transcript_path": "/tmp/s-1.jsonl", "cwd": "/tmp"},
    {"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": "/tmp/a"}, "tool_response": "ok", "tool_use_id": "tu-2", "session_id": "s-1", "transcript_path": "/tmp/s-1.jsonl", "cwd": "/tmp"},
    {"hook_event_name": "PermissionDenied", "tool_name": "Bash", "tool_input": {"command": "rm -rf /"}, "tool_use_id": "tu-3", "reason": "classifier", "session_id": "s-1", "transcript_path": "/tmp/s-1.jsonl", "cwd": "/tmp"}
  ],
  "summaries": {
    "SessionStart": "session start (startup)",
    "SessionEnd": "session end (clear)",
    "Stop": "stopped: done",
    "PostToolUse": "Read done: /tmp/a",
    "PermissionDenied": "permission denied — Bash: rm -rf /",
    "PreToolUse": "Read: /a"
  },
  "pre_tool_use": {"session_id": "s-1", "transcript_path": "/t.jsonl", "cwd": "/repo", "hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "/a"}, "tool_use_id": "tu-9"}
} as const
