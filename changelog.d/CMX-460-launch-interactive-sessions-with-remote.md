### Changed

- **Remote Control is now a setting, off by default: `CHELA_REMOTE_CONTROL=on|off`.** When it
  is on, a restored session (the boot restore and `chela restore --resume`) comes back with
  `--remote-control` under its persisted manual name, so a reboot no longer leaves you
  typing `/remote-control` into every pane. `chela spawn`, the dashboard's New session and
  Telegram `/new` already added the flag. Dispatched runs and judges never get it. If Claude
  Code refuses the flag (a managed `disableRemoteControl`, or the org toggle), the restore
  relaunches the session without it and logs why. **If you relied on the old ON default,
  set `CHELA_REMOTE_CONTROL=on` or turn it on in Settings.** (CMX-460)
