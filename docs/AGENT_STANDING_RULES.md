## Standing rules

These hold for every run. When the orchestrator corrects an agent, the fix lands here.

- Never wrap `chela task-finished` in an outer `timeout`, nor `chela judge self-check` or `chela judge sweep`; they have their own budgets and a kill mid-run loses it. Run `task-finished` alone: it runs the self-check and sweep itself.
- Run `chela task-finished`, `chela judge sweep` and `chela judge self-check` with run_in_background, never as a foreground Bash call; a foreground call hits the Bash tool's time limit (exit 143, "Terminated") while it waits for the suite slot.
- Full suites run one at a time box-wide; waiting for a suite slot is normal, so don't kill or retry it.
- Never run a full suite yourself; `task-finished` runs it in the queue under a memory cap. Run only targeted tests (`pytest tests/test_x.py`), under `timeout`.
- Merge `origin/<base>` into your branch before `task-finished`; it refuses a branch that is behind.
- If a hand-in or self-check is killed (memory, signal), check `git diff` for a leftover mutation, restore it with an edit, then STOP and report to the orchestrator; don't restart it on your own, because a second run can stack on the first.
- If the same failure repeats twice, stop and report instead of looping; a third try rarely differs.
- Never put another issue's id in a PR title or body; Linear auto-closes any issue a merged PR names.
- Don't `pgrep -f "<literal>"`, since it matches your own shell; use a bracketed pattern like `[c]hela …`.
- Reply to the orchestrator with SendMessage, not tmux, so the message carries who sent it.
