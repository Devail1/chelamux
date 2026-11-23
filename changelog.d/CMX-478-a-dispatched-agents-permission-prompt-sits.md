### Fixed

- **The watchdog no longer dismisses a dispatched agent's permission prompt.** When auto mode
  handed a call to a human, the dispatcher's watchdog saw the agent `waiting` and sent Escape
  within seconds, as if it were an unanswerable `AskUserQuestion`. Escape on a permission
  prompt denies the call and interrupts the turn ("Interrupted · What should Claude do
  instead?"), so the agent stopped and nobody could approve it. The watchdog now Escapes (and
  later fails) only a dialog the run's newest `PermissionRequest` hook names as an
  `AskUserQuestion`; a permission prompt, or a dialog it cannot name, stays open for a human.
  (CMX-478)

### Added

- **The orchestrator inbox hears about a dispatched agent's permission prompt at once.** Each
  `PermissionRequest` hook from a running or claimed run's window queues one
  `run_permission_prompt` notice ("✋ CMX-N · @N is waiting on a permission prompt: Bash … —
  answer it in its pane"), deduplicated per prompt, on the next inbox tick. It interrupts the
  digest like a denial does. The needs-input phone push (`CHELA_NOTIFY_URL`, Telegram included)
  now reaches the human too, since the prompt is no longer dismissed before it can fire.
  (CMX-478)
