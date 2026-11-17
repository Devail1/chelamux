### Changed

- **The dashboard hides the Share and Telegram controls when those services aren't
  configured.** With `CHELA_COLLAB_RELAY` unset, "Share current session" is gone from the gear
  menu, the pane menu and the command palette, and Settings > Collaboration is one line:
  "Sharing is off: set `CHELA_COLLAB_RELAY` to enable (see docs/SHARE_SANDBOX.md)". With no
  Telegram bridge configured (`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, read from the env,
  `chela.env` or `secrets.env` the way the bridge reads them), Settings > General > Telegram
  topics is one line pointing at `docs/CONFIG.md`. Both come from configuration, not from
  whether a process is up, so a bridge restart doesn't make the controls flicker. With either
  service configured, its controls are unchanged. (CMX-387)

### Fixed

- **An unset relay reads "off" in Settings, not "(default)".** There is no default relay:
  unset means sharing is off. (CMX-387)
