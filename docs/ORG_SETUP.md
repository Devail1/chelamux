# Running chela at work

A setup for running chela on a machine in an organisation, reached from **one phone** over
**Tailscale**, with **no Telegram bridge** and **no shared sessions**. Nothing here is a new
feature: it is a choice of which services to run, which variables to leave unset, and how to
put the dashboard on a tailnet without exposing anything else.

Every chela command and variable named below exists in this repo. Tailscale and Caddy
snippets are written against their upstream docs; check each one in its own tool before you
rely on it.

## 1. What runs, and what you give up

Run these PM2 apps from [`examples/ecosystem.config.js`](../examples/ecosystem.config.js)
(see [CONFIG.md](CONFIG.md#why-not-a-pm2-env-block) for how they start):

| App | Why |
|---|---|
| `chela-daemon` (`chela run`) | scheduler and notifier (the dispatcher stays off, see below) |
| `chela-dashboard` (`chela dashboard`) | the web dashboard; binds `CHELA_DASHBOARD_PORT` (default 5001) on `127.0.0.1` |
| `chela-agent-terminals` (`chela agent-terminals`) | the ttyd supervisor behind the terminal wall. The wall's tiles are empty without it. Leave it out (and set `CHELA_TERMINALS_ENABLED=false`) only if you do not want terminals in the browser at all |

Installed from PyPI (`uv tool install "chelamux[dashboard]"`), with no checkout for the
ecosystem file's `CHELA_REPO` to point at? The wall's supervisor ships in the package: run
`chela agent-terminals` (under pm2: `pm2 start "$(command -v chela)" --name
chela-agent-terminals --interpreter none -- agent-terminals`). It needs `ttyd` on PATH
(`brew install ttyd` on macOS) and runs on macOS's stock `/bin/bash` 3.2.

**Do not start** `chela-telegram` or `chela-collab`: delete those two lines from your copy of
the ecosystem file, so a cold `pm2 start` cannot bring them back. In `chela.env` and
`secrets.env`, **leave unset**:

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CHELA_TELEGRAM_ALLOWED_USERS`
- `CHELA_COLLAB_RELAY` (it defaults to empty, which turns sharing off)
- `CHELA_REMOTE_CONTROL` (or set it to `off`; off is the default). Remote Control is
  disabled by policy at work, so chela does not add `--remote-control` to the sessions it
  launches. Leave Settings → General → Remote Control off too
  ([CONFIG.md](CONFIG.md#remote-control-chela_remote_control))

`chela doctor` then reports `Telegram bridge: not configured (optional)`, and Settings →
*Connections & Status* shows Collaboration as `Off`.

**Turn dispatch off, explicitly.** This setup uses no workflows and no kanban: no
dispatcher, only the wall, sessions, the sidebar and terminals. Leave
`CHELA_DISPATCH_WORKFLOWS` empty **and** set, in `chela.env`:

```sh
CHELA_DISPATCH=off
```

then restart `chela-daemon` and `chela-dashboard`. The switch says the dispatcher is off by
choice, not by accident: the daemon logs `dispatch: OFF (by choice, CHELA_DISPATCH=off)` at
INFO, `chela doctor` reports it as information instead of a warning, and the dashboard hides
the Work board, **New task** and the sidebar's **Dispatched** section. Settings shows one line
in place of the *Work dispatcher* row. Without the switch, an empty workflow list still
warns, on purpose ([CONFIG.md](CONFIG.md#a-config-is-not-a-capability)).

### On macOS: launchd instead of PM2

PM2 is not required. On a Mac, a launchd **LaunchAgent** starts each service at login and
restarts it if it exits. Use one agent per service, each running `scripts/run-chela.sh` (the
same start script the ecosystem file uses, so `chela.env` is still the only config). For the
dashboard, `~/Library/LaunchAgents/org.chela.dashboard.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>org.chela.dashboard</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string><checkout>/scripts/run-chela.sh</string>
    <string>dashboard</string>
  </array>
  <!-- launchd starts with a bare PATH: name where chela, uv and tmux live. -->
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>            <string><home>/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <true/>
  <key>StandardOutPath</key>   <string><home>/Library/Logs/chela-dashboard.log</string>
  <key>StandardErrorPath</key> <string><home>/Library/Logs/chela-dashboard.log</string>
</dict>
</plist>
```

launchd does not expand `~` or `$HOME`: replace `<checkout>` and `<home>` with absolute
paths. Copy the file for the daemon (`run` instead of `dashboard`, label
`org.chela.daemon`) and for the terminal wall (`<checkout>/scripts/agent-terminals.sh`, no
argument). Then:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.chela.dashboard.plist  # load and start
launchctl kickstart -k gui/$(id -u)/org.chela.dashboard                            # restart
launchctl print gui/$(id -u)/org.chela.dashboard | grep -E 'state|pid|last exit'   # status
tail -f ~/Library/Logs/chela-dashboard.log                                         # logs
launchctl bootout gui/$(id -u)/org.chela.dashboard                                 # stop and unload
```

Wherever this guide says `pm2 restart <app>`, use the `kickstart -k` line for that label.

### What you lose, and what replaces it

**Approving an override or a release.** `chela merge --override` and `chela release` record a
request that a human approves later ([RUNS.md](RUNS.md)). Without Telegram there is no
one-tap button in a chat. The same request is decided from:

- the dashboard's **Pending approvals** pill, which opens a sheet with Approve and Deny on
  every pending request;
- the confirm page at `/override/<id>` on the dashboard;
- `chela merge-approve <id>` (or `--deny`) in a plain terminal on the host. With no id it
  lists the pending requests.

All three go through the same decide path, which re-checks the PR head, CI and mergeability
at the moment you approve.

**Alerts.** Telegram topics were a live feed per agent. Without them:

- the **inbox**: run and agent events are pushed into the orchestrator session (a session
  becomes it with `chela watch`), and every one of them is also kept in the dashboard's
  decisions log, the inbox button in the sidebar header, whether or not an orchestrator is
  running. `chela watching` shows the watches and the queued events;
- `chela digest` prints the brief of what waited (needs you, waiting, done, changed);
  `chela digest --send` delivers it now;
- the full event log, with `chela events`.

Phone push is a separate, optional knob: `CHELA_NOTIFY_URL` (ntfy, a Telegram Bot API URL, or
a generic webhook, auto-detected). For this setup, leave it unset or point it at a webhook
your organisation runs. A public `ntfy.sh` topic is unauthenticated and a third party, so
anything it carries (run titles, PR links) leaves the org (see
[CONFIG.md](CONFIG.md#dont-clone-someone-elses-chelaenv)).

**Share and guest access.** Not available. With `CHELA_COLLAB_RELAY` unset the dashboard
refuses to start a share (`Sharing is off`), so there are no guest links, no presence, and no
shared typing. There is no other way to let a second person into a session; anyone who needs
the dashboard needs their own grant on the tailnet (section 3).

### Controls the UI hides with those services off

The dashboard reads both from configuration when the page loads, so they are hidden as long
as the variables stay unset, and a bridge restart doesn't bring them back for a moment
([CMX-387](https://linear.app/chelamux/issue/CMX-387)):

- With `CHELA_COLLAB_RELAY` unset, **Share current session** is gone from the primary menu,
  each pane's header menu and the command palette. Settings → **Collaboration** is one line:
  "Sharing is off: set `CHELA_COLLAB_RELAY` to enable (see docs/SHARE_SANDBOX.md)". Where a
  relay is set, its *Relay* row names it, and an unset one reads `off`.
- With `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` unset (in the environment, `chela.env` and
  `secrets.env`), Settings → General → **Telegram topics** is one line pointing at
  [CONFIG.md](CONFIG.md#secrets) instead of the delete-on-close toggle and the archived-topic
  cleanup.

Reload the dashboard after you set or unset either one.

## 2. Remote access over Tailscale

The dashboard stays bound to `127.0.0.1`. Do not set `CHELA_DASH_HOST` to another address:
the module docstring of `chela/dashboard/app.py` explains why. The dashboard has **no
built-in auth**, and the terminal wall is a writable shell, so exposing it on a network is
remote code execution. If you bind a non-loopback host anyway, the dashboard turns the wall
off unless `CHELA_TERMINALS_EXPOSE=true`. Do not set that here.

Something in front of `127.0.0.1:<port>` puts it on the tailnet. Two ways:

### (a) Caddy with caddy-tailscale, its own node per site

[caddy-tailscale](https://github.com/tailscale/caddy-tailscale) embeds a Tailscale node
(tsnet) inside Caddy, so each site is a separate tailnet node with its own name, tags and
certificate. The host itself does not need to run `tailscaled` or join the tailnet. This is
how the maintainer runs chela.

Build Caddy with the plugin (`xcaddy build --with github.com/tailscale/caddy-tailscale`), then:

```caddyfile
{
	tailscale {
		# An auth key generated with the tag below (Settings > Keys in the admin console).
		auth_key {env.TS_AUTHKEY}
		state_dir /var/lib/caddy/tailscale
	}
}

https://<node-name>.<tailnet-name>.ts.net {
	bind tailscale/<node-name>
	tls {
		get_certificate tailscale
	}
	tailscale_auth
	reverse_proxy 127.0.0.1:5001 {
		# The dashboard records Tailscale-User-Login as who approved (section 4).
		header_up Tailscale-User-Login {http.auth.user.tailscale_user}
		header_up Tailscale-User-Name {http.auth.user.tailscale_name}
	}
}
```

Replace `5001` if you set `CHELA_DASHBOARD_PORT`. Generate the auth key **with
`tag:org-chela`** so the node is tagged from its first login. Check the global options and
key handling against the plugin's README for the version you build.

`tailscale_auth` asks the embedded node who is calling, and the two `header_up` lines pass
that to chela. Without them the dashboard never learns a name, and every approval reads
`dashboard` instead of the person who made it. Three details:

- **`tailscale_user`, not `tailscale_login`.** `tailscale_user` is the full login (an email
  address), which is what chela records. The plugin's `tailscale_login` is only the part
  before the `@`.
- **No separate strip line.** `header_up` with a value replaces any copy the client sent, so
  a client cannot claim to be someone else.
- **User-owned devices only.** `tailscale_auth` identifies a request only from a device
  owned by a user on the tailnet, such as your phone or laptop. A request from a tagged
  device carries no user, so it gets no name.

Then set `CHELA_DASHBOARD_IDENTITY_HEADER=tailscale` (section 4). The name is attribution,
not authority: it records who approved, and the tailnet policy still decides who gets in.

#### Signing the node in without an auth key

You do not have to create or store an auth key at all. Leave `auth_key` out, and give the
node its tags in a node block named after the `bind` target:

```caddyfile
{
	tailscale {
		state_dir /var/lib/caddy/tailscale
		<node-name> {
			tags tag:org-chela
		}
	}
}
```

`tag:org-chela` must already exist in the policy's `tagOwners` (section 3), and you must be
one of its owners, or the login is refused. On the first start the embedded node has no
state, so it prints a login URL to **Caddy's log**: open it, sign in, and the node joins
tagged. After that the key lives in `state_dir`, and later starts sign in silently. Check the
`tags` option against the plugin's README for the version you build.

#### Pitfalls

- **One Caddy per `state_dir`.** Two Caddy processes on the same `state_dir` run the same
  node key twice. The admin console then shows **"Duplicate node key"**, and the node drops
  in and out. Before you start Caddy, check that no other one is running
  (`pgrep -l '[c]addy'`).
- **A `| tee` can outlive its window.** If you run Caddy in a tmux window as
  `caddy run … | tee caddy.log`, killing or respawning the window can leave the `caddy`
  process alive, still holding the node, and the next start is the duplicate above. Stop it
  properly (`caddy stop`, Ctrl-C in its window, or the service manager), or better, run it
  as a service and log to a file instead of a pipe.
- **The Tailscale app on the same host.** With (a) the host itself is not meant to be on the
  tailnet. If the Tailscale app (or `tailscaled`) is also installed, it can sign the whole
  host back in, and the policy in section 3 then has a second node to contain. For (a),
  uninstall it from the host; the embedded node does not need it.

### (b) `tailscale serve`

If the host runs `tailscaled` and has joined the tailnet itself:

```bash
sudo tailscale up --advertise-tags=tag:org-chela
tailscale serve --bg 5001          # https://<node-name>.<tailnet-name>.ts.net -> 127.0.0.1:5001
tailscale serve status             # what is served
tailscale serve reset              # stop serving
```

`tailscale serve` proxies to localhost and sets `Tailscale-User-Login` itself for requests
from a user's (untagged) device. The difference from (a): here the **whole host** is a
tailnet node, so the policy in section 3 must keep it from reaching anything, not just the
dashboard.

### On the phone: a home-screen app that does not look like your personal one

The dashboard installs as a standalone app: in Safari, Share → **Add to Home Screen**; in
Chrome, **Install app**. If you also run a personal chela, the two must not be confused on
the home screen. Set, in the dashboard's environment (`chela.env`):

| Variable | Values | Effect |
|---|---|---|
| `CHELA_DASHBOARD_ICON_VARIANT` | `default` (unset) or `work` | `work` draws the same chela mark with a **briefcase badge** in its bottom-right corner, so the two icons differ in shape, not only in colour, and gives the browser bar a different `theme-color` |
| `CHELA_DASHBOARD_APP_NAME` | any text (default `chela`) | the label under the icon (the manifest's `short_name` and the iOS title) and the page title, `<name> – Mission Control` |

For example `CHELA_DASHBOARD_ICON_VARIANT=work` and `CHELA_DASHBOARD_APP_NAME="chela work"`;
put your organisation's name in the label if you want it, since nothing org-specific ships with
chela. Both are read from the environment only, never from `~/.chela/config.json` (a file the
dashboard itself can write), like the identity settings in section 4. An unknown variant is
refused: the dashboard answers 503 and names the bad value, rather than quietly drawing the
default icon. `pm2 restart chela-dashboard` after a change, then remove and re-add the
home-screen icon: a phone keeps the icon and label it saved.

**If an iPhone says "Safari can't open the page because the server can't be found"** for the
`*.ts.net` address, the phone is not resolving tailnet names. In the Tailscale app, turn on
**Use Tailscale DNS**. If it still fails, turn off **iCloud Private Relay** for the network
the phone is on (Settings → Wi-Fi → the network's ⓘ): Private Relay can send Safari's
lookups around Tailscale's resolver.

## 3. The tailnet policy

The goal: one person, from their phone only, can reach the chela node on port 443, and the
chela node can reach nothing.

> **Check this in the policy editor before you save it.** It is a sketch of the shape, not a
> tested policy. Use the console's **Preview** tab to see which devices each rule lets in.

```jsonc
{
  "tagOwners": {
    "tag:org-chela": ["<you>@<your-domain>"]
  },

  "postures": {
    // node:os is reported by the Tailscale client on the device.
    "posture:phone": ["node:os IN ['ios', 'android']"]
  },

  "grants": [
    {
      // One user, from a device that matches posture:phone, to the chela node, HTTPS only.
      "src": ["<you>@<your-domain>"],
      "srcPosture": ["posture:phone"],
      "dst": ["tag:org-chela"],
      "ip": ["tcp:443"]
    }

    // No grant has tag:org-chela in "src". The org machine can open no connection to
    // your laptop, your phone, or anything else on the tailnet.
  ]
}
```

**Replacing the default policy.** A new tailnet starts with an allow-all rule. The moment you
replace it with grants like the above, **everything not granted is denied**, including
services you already use between your own devices (SSH to your laptop, a NAS, exit nodes,
subnet routers). Write an explicit grant for each one you keep, and look at the Preview
before you save.

**Why posture and not a tagged phone.** Tagging the phone would also restrict the source, but
a tagged device stops belonging to a user: it is owned by the tag. Its requests then carry no
user login, so `tailscale serve` (and any identity-aware front) has no name to pass to the
dashboard. Approvals would be recorded as plain `dashboard`, not as you. Keeping the phone
user-owned and matching it on posture keeps your identity on every request, which per-user
attribution of approvals ([CMX-385](https://linear.app/chelamux/issue/CMX-385/dashboard-attribute-approvals-to-the-verified-user-from-a-trusted))
depends on. Note that `node:os` is what the client reports, not an attestation: it narrows
which of your devices are let in, it does not prove a device is a phone.

**Alternative for exactly one phone: an IP alias.** Instead of user plus posture, name the
phone's tailnet IP in `hosts` and grant from that alias:

```jsonc
{
  "hosts": {
    "my-phone": "<phone-ip>/32"
  },
  "grants": [
    { "src": ["my-phone"], "dst": ["tag:org-chela"], "ip": ["tcp:443"] }
  ]
}
```

This still keeps identity: the phone stays user-owned (it is not tagged), so its requests
still carry your login. It is stricter than posture, which lets in every phone you own; the
alias lets in one device. The caveat: tailnet IPs are not permanent. If the phone is removed
from the tailnet and re-added (or replaced), it gets a new IP, and the old one can later be
given to **another device**, which the alias then lets in. Update or delete the alias
whenever the phone leaves the tailnet.

## 4. Hardening checklist

- [ ] **Never Funnel.** `tailscale funnel` publishes a node to the public internet. With no
      auth in the dashboard, that is a public shell. Neither setup in section 2 needs it.
- [ ] **Turn on [Tailnet Lock](https://tailscale.com/kb/1226/tailnet-lock).** Without it,
      whoever controls the coordination server (or your Tailscale account) can add a node
      that your devices trust. With it, a new node needs a signature from a device you hold.
- [ ] **Node names that reveal nothing.** Every HTTPS certificate Tailscale issues is
      published in public Certificate Transparency logs, with the full
      `<node-name>.<tailnet-name>.ts.net` name. Do not put the org's name, a project name,
      or a person's name in the node name.
- [ ] **Identity is attribution, not authority.** The dashboard reads `Tailscale-User-Login`
      only to record *who* approved and who granted access. It never checks it. The tailnet
      grant is the only thing that decides who gets in; anyone the policy lets through can
      approve. Set `CHELA_DASHBOARD_IDENTITY_HEADER=tailscale` so the header is believed only
      from a loopback peer (refused and logged from anywhere else), and every mutating action
      is recorded with its `by`; see "Who did it" in [DASHBOARD.md](DASHBOARD.md). A process
      on the host itself can still send the header, and a second proxy must overwrite or strip
      it on client requests (the Caddy block above overwrites it). For a login that cannot be forged even
      locally, use the `cloudflare-access` mode (a verified JWT). In `off` and `tailscale`
      mode a local process can also approve over the dashboard's HTTP API, which the
      merge-gate hook misses (CMX-392).
- [ ] **Check the Claude account.** The line under the dashboard's sidebar head is the
      account the `claude` CLI is signed in to (`claude auth status --json`): it should name
      the org's account, not a personal one. It warns when the CLI is logged out or the login
      expires within `CHELA_CLAUDE_LOGIN_WARN_DAYS` (default 3). After an account switch, a
      session that started before it is badged "may still use" the old account: restart it.
- [ ] **Keep the dashboard on loopback.** `CHELA_DASH_HOST` unset (it defaults to
      `127.0.0.1`), `CHELA_TERMINALS_EXPOSE` unset.
- [ ] **Turn off Caddy's admin API.** Put `admin off` in the Caddyfile's global options
      block. Otherwise Caddy listens on `localhost:2019`, and any local process can replace
      its config, including the `header_up` lines that set the identity header. With it off,
      reload by restarting Caddy.
- [ ] **Turn off the plugin's web UI.** In option (a), set `webui false` in the `tailscale`
      block (or the node block), so the embedded node serves no Tailscale web interface.
- [ ] **Lock down the tsnet state dir.** `state_dir` holds the node's private keys. Make the
      directory mode `700` and its files `600`, owned by the user Caddy runs as:
      `chmod 700 <state_dir> && chmod 600 <state_dir>/*`.
- [ ] **Keep the keys out of backups.** On macOS, `tmutil addexclusion <state_dir>` keeps the
      state dir out of Time Machine, so a copy of the node's keys does not sit on a backup
      disk. Exclude it from any other backup tool too.

## 5. What this does not solve

- **Your organisation's rules.** Joining an org machine to a personal tailnet, and keeping
  org code on a machine that runs chela, are policy questions this guide cannot answer. Ask
  IT first, and get the answer in writing.
- **Dispatched agents run unsandboxed on the host.** A dispatched coding agent runs as your
  user, with your files, credentials and network
  ([SANDBOX_BOUNDARY.md](SANDBOX_BOUNDARY.md),
  [TRUST_AND_APPROVAL_POSTURE.md](TRUST_AND_APPROVAL_POSTURE.md)). The tailnet policy limits
  who reaches the dashboard; it does not limit what an agent on the host can do. Treat the
  host as able to run anything the agents run, including reaching any network the host
  itself can reach outside Tailscale.
- **An open LAN port from the embedded node.** With option (a), tsnet's peerapi (the
  node-to-node API Tailscale uses for things like Taildrop) listens on `0.0.0.0:<port>` on
  the host, a port chosen at start. This is how it behaves on macOS, where tsnet runs on
  netstack. Callers that are not tailnet peers are dropped (the log shows "unknown peer"),
  so it serves nothing to them, but it is still a port open to the local network, visible to
  a port scan. On a managed Mac the application firewall may be controlled by IT and not
  changeable from the command line, so you may not be able to close it. Treat it as a known
  exposure, and tell IT about it if your setup needs their approval.

## 6. The Cloudflare alternative

Cloudflare Access in front of a `cloudflared` tunnel can also put the dashboard behind a
login without opening a port. The trade-off is that Cloudflare terminates TLS at its edge and
sees every request and response in clear text, including the terminal wall's keystrokes and
output. Under a **personal** Cloudflare account, that routes org data through a third party
you contracted personally, not the organisation. If the org already runs Cloudflare Access
under its own account, ask IT whether chela may sit behind it; otherwise prefer Tailscale,
where traffic is end-to-end encrypted between your phone and the node.
