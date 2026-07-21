# Security Policy

## ⚠️ Read this before you run chela

chela is an **orchestrator for autonomous coding agents**. It launches Claude Code
sessions in tmux windows and, in dispatch mode, drives them to pick up tasks, write
code, run commands, and open pull requests **with little or no human confirmation**
(the default agent command is `claude --permission-mode auto`).

That means: **running chela runs software that can read, write, and execute arbitrary
code on the host it runs on**, under whatever identity started it. Treat it like giving
a capable, fast, and occasionally wrong developer an unattended shell on your machine.

Design your deployment around that fact. In particular:

- **Isolate the host.** Prefer a dedicated user account, VM, container, or cloud box
  over your primary workstation. Don't run it on a machine that holds credentials,
  private keys, or data you can't afford an agent to read or a bad task to damage.
- **Assume agents can see their environment.** Any secret in the environment,
  `~/.config`, cloud CLI credentials, SSH keys, or reachable network services is
  reachable by a running agent. Scope the host's credentials to the minimum.
- **The dispatcher is not a sandbox.** It bounds *what work is claimed*, not *what a
  claimed task can do*. Isolation is your responsibility, at the OS/VM/container layer.
- **Review before you widen authority.** Auto-merge, orchestrator auto-arming, and
  scheduled dispatch each hand more autonomy to the loop. Turn them on deliberately.

## Network exposure

- **The dashboard and the embedded terminals are writable shells.** The ttyd wall
  exposes live, interactive terminals over HTTP. Anyone who can reach that port can
  type into your sessions.
- **Bind to localhost and put an authenticated, encrypted layer in front.** The app
  binds to `127.0.0.1` by default. Expose it only through something that authenticates
  and encrypts — [Tailscale](https://tailscale.com/) (`tailscale serve`), an SSH
  tunnel, or a reverse proxy that adds auth. **Never bind it to `0.0.0.0` on an
  untrusted network or the public internet.**
- **Terminal sharing / the relay is sensitive.** Share links grant terminal access for
  the life of the session. Treat them like passwords; un-sharing and restarts revoke
  them.

## Secrets

- chela reads its own secrets (e.g. the Telegram bot token behind `chela-telegram`,
  `CHELA_NOTIFY_URL`) from the **environment**, never from committed files. Keep it that
  way — put secrets in your shell/PM2 environment or an untracked `.env` the process
  loads, and never commit them.
- If a secret is ever committed, **rotate it at the provider** — don't just delete or
  obfuscate it. Git history (and GitHub's `refs/pull/*`) preserves removed content.

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue for anything
exploitable.

- Preferred: open a **private security advisory** via this repository's
  *Security → Report a vulnerability* (GitHub Private Vulnerability Reporting).
- <!-- Optional: add a security contact email here if you want a non-GitHub channel. -->

Please include what you found, how to reproduce it, and the impact. We'll acknowledge
and work on a fix; coordinated disclosure is appreciated.

## Scope

This policy covers the chela codebase in this repository. It does **not** cover the
security of Claude Code itself, tmux, ttyd, Tailscale, or any other dependency — report
issues in those to their respective projects.
