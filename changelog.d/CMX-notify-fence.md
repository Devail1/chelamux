### Fixed

- **The test suite can no longer push a real notification to the operator's phone.**
  `notify.enabled()` is `bool(NOTIFY_URL)`, read from the environment at import, so on the
  machine that actually runs chela any test reaching a `notify.send` call site pushed a real
  ntfy/Telegram message. `test_auto_apply_sweep_never_calls_apply_with_a_bespoke_repo_arg`
  did exactly that — it drives the real `auto_apply_sweep()` and, alone among its three
  siblings, never stubbed `notify`. CMX-115 had already stripped `CHELA_NOTIFY_URL` from
  every tmux-spawned agent and judge, which is why this stayed hidden: that covers the
  dispatcher's own paths, but not a maintainer running `pytest` or `chela judge run` from
  their own shell. `tests/conftest.py` now fences outbound notifications the way it already
  fences live `~/.chela` state — `NOTIFY_URL` blanked so every call site gates off, and
  `_post` (the single transport funnel) raising `LiveNotificationEscape` if anything reaches
  it anyway. Like `LiveStateEscape` it derives from `BaseException`, because `notify.send`
  wraps its transport in `except Exception` and would otherwise swallow the guard and report
  an ordinary send failure.
