### Added

- **`chela telegram send-file <path>` posts a file to your own window's Telegram topic.**
  The topic comes from the bridge's bindings. The bot token and chat id are read the way
  `chela-telegram` reads them (environment, then `chela.env`, then `secrets.env`), so an
  agent needs no setup. On success it prints the message id and topic. It exits 1 with one
  line saying why when:
  - Telegram is not set up: the `telegram` extra is missing, there is no bot token, or the
    window has no topic. The message names what is missing and points at
    `skills/telegram-setup/SKILL.md`.
  - The file is over Telegram's 50 MB bot limit.
  - Telegram refuses the file.

  `--to @N` sends to another window's topic and `--caption` adds a caption. The command
  lives under `chela telegram`, so core `chela --help` does not list it. (CMX-370)
