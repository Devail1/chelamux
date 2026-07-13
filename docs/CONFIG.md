# Config — the environment is the single source of truth

chela is configured by environment variables (the full table is in the
[README](../README.md#config-environment)). This document is about *where those variables
are written down*, which is a separate question, and one chela got wrong.

**One file: `$CHELA_DIR/chela.env`.** Everything reads it — `chela` itself sources it at
import (`chela/config.py`), and `scripts/run-chela.sh` sources it for anything a process
manager starts. Nothing else may carry a copy.

```bash
cp examples/chela.env ~/.chela/chela.env     # then edit
chela doctor                                 # does what's running still match the file?
```

## Precedence

1. **An exported variable** — `CHELA_TMUX_SESSION=other chela status` still works. An
   override has to stay possible.
2. **`$CHELA_DIR/chela.env`** — the source of truth for an install.
3. **The built-in defaults** — chela runs correctly from a plain shell with nothing
   exported and no env file at all. A fresh install is not a broken one.

Python (`os.environ.setdefault`) and shell (`scripts/chela-env.sh`) implement exactly this
order, so a value resolves the same whichever reads it.

Two variables are about the file itself and can only come from the environment:
`CHELA_DIR` (which is how the file is *found* — it cannot relocate itself) and
`CHELA_ENV_FILE` (a different path; empty disables the file, which is what the test suite
does so a developer's real config can never leak into a unit test).

## Why not a PM2 `env:` block

Because config in two places drifts, and the drift is silent.

chela's `ecosystem.config.js` duplicated `CHELA_TMUX_SESSION` into three `env:` blocks.
When the tmux session was renamed, all three still named the old one — for a day. Nothing
broke, because the *running* processes had been fixed by hand; a clean `pm2 start` would
have brought the entire fleet up against a session that no longer existed.

So `examples/ecosystem.config.js` has **no `env:` block at all**. Every app starts through
`scripts/run-chela.sh`, which sources `chela.env`. The process manager carries no config;
it carries a pointer to the thing that does.

### Migrating an ecosystem file that has `env:` blocks

`pm2 restart --update-env` **merges** the environment — it will not remove a variable you
deleted from the file. Clearing one really does require:

```bash
pm2 delete chela-daemon chela-dashboard chela-telegram
pm2 start ~/.chela/ecosystem.config.js && pm2 save
```

and it must be run from a shell that is **not inside tmux**: `config.current_session()`
falls back to deriving the session from `$TMUX_PANE`, so a leaked pane makes a service
silently target whatever session that pane was in — a `webterm_*` *mirror*, in the case
that bit us. `run-chela.sh` strips `TMUX`/`TMUX_PANE` for exactly this reason.

## The dashboard port, and why it is the case that proves the rule

The port is not just config. It is baked into the **Claude Code hooks plugin** as a
literal URL (Claude Code does not expand env vars in a hook `url`), and `chela plugin`
renders that manifest from a **different process** than the dashboard.

`chela dashboard --port 5005` put the port in *one process's* `os.environ`. `chela plugin`
saw nothing, fell back to the default 5001, and wrote `http://127.0.0.1:5001/hooks/…` into
the manifest. Every hook then POSTed into a closed socket — and a hook that cannot reach
the daemon **fails open by design** (it must never wedge a live agent), so nothing
surfaced. The whole feature did nothing, silently, for a day.

Two things fix it:

* **The env file is where the port lives.** The dashboard binds `CHELA_DASHBOARD_PORT`.
  `--port` still works as a one-off override, but it is then *not* the source of truth.
* **The dashboard publishes the port it actually bound** to `$CHELA_DIR/dashboard.port`
  at startup. `config.live_dashboard_port()` — what `chela plugin` renders, and what
  `chela doctor` checks — prefers that over the configured value, so the manifest names a
  port something is really listening on **even when someone passes `--port` by hand**. A
  file whose pid is gone is treated as no dashboard at all, so a crashed instance cannot
  keep pointing hooks at a dead port.

A disagreement between the two is never resolved quietly: `chela plugin` prints it, and
`chela doctor` exits non-zero.

## `chela doctor`

```
$ chela doctor
✓ env file /home/you/.chela/chela.env (5 vars)
✓ the running environment agrees with the env file
✓ tmux session 'chela' (CHELA_TMUX_SESSION)
✗ dashboard is LISTENING on 5098, but the config says 5099
    A --port flag beats the env, and the env is supposed to be the source of truth. …
✓ rendered plugin posts to port 5098

1 problem(s) — see above.
```

It compares the *running* config against the file: stale environment variables (the
`--update-env` merge), a dashboard on a port the config does not know about, a rendered
plugin baked against a port that has since moved, a session name derived from a leaked
tmux pane, and an env file that tries to set its own `CHELA_DIR`. Exit code `1` means
something is broken **right now**; warnings are differences worth knowing about.

## Secrets

`chela.env` is the pasteable file — you diff it, template it, copy it from
`examples/chela.env`. A bot token has no business in it.

Secrets go in **`$CHELA_DIR/secrets.env`** (`chmod 600`), which `run-chela.sh` sources
separately and only the services that need one ever read:

```bash
umask 077 && printf 'TELEGRAM_BOT_TOKEN=…\nTELEGRAM_CHAT_ID=…\n' > ~/.chela/secrets.env
```

Neither file is ever committed. Both live under `$CHELA_DIR`, which is not the repo.
