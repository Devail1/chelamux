### Added

- **`chela send-file <path>` posts a file to your own window's Telegram topic.** It reads
  the topic from the bridge's bindings and the bot token and chat id the way
  `chela-telegram` does (environment, then `chela.env`, then `secrets.env`), so an agent
  needs no setup. It prints the message id and topic on success, and exits 1 with the
  reason when the window has no topic, the file is over Telegram's 50 MB bot limit, or
  Telegram refuses it. `--to @N` sends to another window's topic, `--caption` adds a
  caption. `chela telegram --help` and the Telegram skills now point at it. (CMX-370)
