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

## A config is not a capability

The dispatcher was off for nine hours and every surface said healthy: `chela doctor` was
all-green, the daemon logged nothing, the dashboard showed a workflow. `CHELA_DISPATCH_WORKFLOWS`
had gone missing from the env file, so `DISPATCH_WORKFLOWS` was `[]`, so the daemon's
`if DISPATCH_WORKFLOWS:` guard skipped the dispatcher **and the reconcile loop that rides
the same tick** — no task was ever claimed, and a merged PR's run sat in `awaiting_review`
forever, holding the only concurrency slot. **The single tell was the ABSENCE of a log
line. Nobody reads an absence.** Doctor was green because the running environment agreed
with the env file: they did agree, and they were both wrong. *Checking that two copies of
a fact match is not checking the fact.*

So chela treats "what the daemon can DO" as a first-class, observable thing:

* **The daemon announces every capability at startup — ON *and* OFF** (`chela/capabilities.py`).
  Anything behind an `if <config>:` guard says which it is; the ones whose absence is a
  foot-gun (dispatch, and the reconciliation coupled to it) say so at **WARNING**, naming
  the variable and the fix. An empty config stays a *valid* state; it stops being an
  *invisible* one. Startup only — a per-tick line would bury the log by morning.
* **The daemon publishes what it really came up with** to `$CHELA_DIR/daemon.json` — the
  same pattern as `dashboard.port`, and for the same reason: `chela doctor` and the
  dashboard are **different processes**, and their env is not evidence about the daemon's.
  A file whose pid is gone is no daemon at all.
* **`chela doctor` asserts the capability, not the config**: the dispatcher is on (a
  `WARN`, never a silent pass, when it is not), each configured `WORKFLOW.md` exists and
  parses, its tracker file is there, and the daemon *that is actually running* has it on —
  an ERROR if the running daemon and the config disagree. With no daemon running, doctor
  says out loud that it is **inferring** from config rather than observing.
* **The dashboard's Settings drawer shows it too** — a *Daemon* row, and a *Work dispatcher*
  row that reads `Off` when the running daemon has it off, however many `WORKFLOW.md` files
  are lying around on disk.

`examples/chela.env` therefore ships `CHELA_DISPATCH_WORKFLOWS=` **empty but present**,
with the coupling written down next to it. A variable you can see and left empty is a
choice; a variable that is simply gone is a bug waiting for nine hours.

## The runtime-truth registry

The same bug landed nine times: **a fact lives in two places across a process boundary, and
the checker reads the copy it OWNS instead of the copy that GOVERNS BEHAVIOUR.** The plugin
we rendered said port 5001 while the dashboard had *bound* 5005; the env file said dispatch
ON while the *daemon* had come up OFF; the rendered manifest said `timeout: 120` while the
*installed* one — the only one agents load — still said `2`; `pytest -q` said 980 green
while three JS suites were executed by nothing and one of them was red. Every one of them
was green everywhere while the feature was dead.

`chela/runtime_truth.py` is the answer: **one entry per fact chela depends on**, naming both
halves — `declared_by` (where *we* write it) and `owned_by` (who actually governs the
behaviour: the bound socket, the running daemon, Claude Code's plugin cache, tmux, the
pytest collector) — plus a `read_back` that asks the owner. Two rules, in code:

* **A fact chela owns: the process that ACTS publishes what it actually DID.** Not what it
  was configured to do (`dashboard.port`, `daemon.json`, the run row's `window_id`). Every
  reader reads that publication, never the config.
* **A fact another system owns: read back from the owner.** You cannot infer the installed
  plugin manifest, or whether a tmux window is still alive, from your own copy. You ask.

**`chela doctor` is generated from it** — for every fact: read the declared value, read back
the owned value, compare, report. There is no hand-written check left, because every
hand-written check acquires a private blind spot (the drift check that compared only the
*first* hook read green on a stale manifest; the test wrapper that named *one* `.mjs` file
left three suites unrun). An enumerated check structurally cannot. **An owner that cannot be
read is reported LOUDLY as `CANNOT VERIFY` — never as a silent pass:** a doctor that goes
green because it could not look is the bug, one level up.

The registry is an artifact too, so it is fenced by the bar it exists to enforce: every
entry must have a test in `tests/test_runtime_truth.py` that **corrupts the owned value and
asserts doctor goes red, naming the fact**. A check that has never been seen to go red is
not a check.

## `chela doctor`

```
$ chela doctor
✓ env file /home/you/.chela/chela.env (5 vars)
✓ the running environment agrees with the env file
✓ tmux session 'chela' exists (CHELA_TMUX_SESSION)
✗ dashboard is LISTENING on 5098, but the config says 5099
    A --port flag beats the env, and the env is supposed to be the source of truth. …
✓ rendered plugin posts to port 5098
✗ the INSTALLED plugin disagrees with the one chela renders — THE HOOKS THAT RUN ARE STALE
    Agents do not read the manifest chela renders. They read: …
✓ daemon running (pid 4242, session chela) — capabilities read from it, not from config
✓ Scheduler: ON
! Work dispatcher: OFF
    CHELA_DISPATCH_WORKFLOWS is empty — NO task will ever be claimed from a tracker — fix: …
! Run reconciliation: OFF
    OFF FOR THE SAME REASON — reconciliation rides the dispatch tick, so a merged PR's run
    sits in `awaiting_review` forever, holding its concurrency slot
! run CMX-70 claims window @31, but tmux has no such window
✓ pytest executes all 6 JS suite(s)

2 problem(s) — see above.
```

Every line is one registry fact, asked of its owner: the env file vs the environment the
processes really carry, the configured port vs the socket really bound, the manifest chela
renders vs **the one Claude Code installed and agents load**, this shell's config vs the
capabilities the **running** daemon published, each `WORKFLOW.md` and the tracker it claims
to read, the dispatch hold, the tmux session and the windows in-flight runs claim, and the
`.test.mjs` suites the **pytest collector** actually executes. Exit code `1` means something
is broken **right now**; warnings are differences worth knowing about — including a
subsystem that is off, which is never reported as green.

Adding a fact is one entry in `runtime_truth.facts()` (plus its red test). No new doctor
code, and — the point — no way to forget the check.

## Secrets

`chela.env` is the pasteable file — you diff it, template it, copy it from
`examples/chela.env`. A bot token has no business in it.

Secrets go in **`$CHELA_DIR/secrets.env`** (`chmod 600`), which `run-chela.sh` sources
separately and only the services that need one ever read:

```bash
umask 077 && printf 'TELEGRAM_BOT_TOKEN=…\nTELEGRAM_CHAT_ID=…\n' > ~/.chela/secrets.env
```

Neither file is ever committed. Both live under `$CHELA_DIR`, which is not the repo.

### Don't clone someone else's `chela.env`

`chela.env` carries no secrets, but it does carry **per-install** values — start from
`examples/chela.env` and set your own rather than copying another machine's verbatim:

- **`CHELA_NOTIFY_URL`** is *yours*. An `ntfy.sh` topic is **unauthenticated** — anyone who
  knows the topic name can both read and publish to it — so it is effectively a shared
  secret. Inherit someone else's and your update / needs-input pings land on *their* phone
  (and they can read yours). Pick your own topic (`ntfy.sh/chela-<you>-<random>`).
- **Paths** (`CHELA_DISPATCH_WORKFLOWS`, …) are absolute and machine-specific — another
  install's point at directories you don't have.
- `CHELA_COLLAB_RELAY` is the one value that is *meant* to be shared: two installs that want
  to share terminal sessions point at the **same** relay on purpose.
