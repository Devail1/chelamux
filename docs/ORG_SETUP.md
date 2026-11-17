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
| `chela-daemon` (`chela run`) | scheduler, dispatcher, notifier |
| `chela-dashboard` (`chela dashboard`) | the web dashboard; binds `CHELA_DASHBOARD_PORT` (default 5001) on `127.0.0.1` |
| `chela-agent-terminals` | the ttyd supervisor behind the terminal wall. The wall's tiles are empty without it. Leave it out (and set `CHELA_TERMINALS_ENABLED=false`) only if you do not want terminals in the browser at all |

**Do not start** `chela-telegram` or `chela-collab`: delete those two lines from your copy of
the ecosystem file, so a cold `pm2 start` cannot bring them back. In `chela.env` and
`secrets.env`, **leave unset**:

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CHELA_TELEGRAM_ALLOWED_USERS`
- `CHELA_COLLAB_RELAY` (it defaults to empty, which turns sharing off)

`chela doctor` then reports `Telegram bridge: not configured (optional)`, and Settings →
*Connections & Status* shows Collaboration as `Off`.

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

### Controls the UI still shows with those services off

These stay visible, though none of them can reach anything:

- **Share current session** in the primary menu, in each pane's header menu, and in the
  command palette. Choosing it shows the `Sharing is off` error.
- Settings → **Collaboration**: the display name, the *Relay* row (which reads `(default)`
  when the relay is unset, though the default is off), and the **Guest typing** toggle.
- Settings → General → **Telegram topics**: the *delete a topic when its window closes*
  toggle and the archived-topic cleanup.

Hiding them when the matching service is off is not supported yet; it is a follow-up.

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
	reverse_proxy 127.0.0.1:5001 {
		# The dashboard records Tailscale-User-Login as who approved (section 4).
		# Drop any copy a client sent, so it cannot claim to be someone else.
		header_up -Tailscale-User-Login
	}
}
```

Replace `5001` if you set `CHELA_DASHBOARD_PORT`. Generate the auth key **with
`tag:org-chela`** so the node is tagged from its first login. Check the global options and
key handling against the plugin's README for the version you build.

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
      only to record *who* approved (`dashboard:<login>`) and who granted access. It never
      checks it. The tailnet grant is the only thing that decides who gets in; anyone the
      policy lets through can approve. Strip the header from client requests at the proxy
      (as in the Caddy block above) so the record cannot be forged. Approval by verified user
      is tracked in [CMX-385](https://linear.app/chelamux/issue/CMX-385/dashboard-attribute-approvals-to-the-verified-user-from-a-trusted)
      and not supported yet.
- [ ] **Keep the dashboard on loopback.** `CHELA_DASH_HOST` unset (it defaults to
      `127.0.0.1`), `CHELA_TERMINALS_EXPOSE` unset.

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

## 6. The Cloudflare alternative

Cloudflare Access in front of a `cloudflared` tunnel can also put the dashboard behind a
login without opening a port. The trade-off is that Cloudflare terminates TLS at its edge and
sees every request and response in clear text, including the terminal wall's keystrokes and
output. Under a **personal** Cloudflare account, that routes org data through a third party
you contracted personally, not the organisation. If the org already runs Cloudflare Access
under its own account, ask IT whether chela may sit behind it; otherwise prefer Tailscale,
where traffic is end-to-end encrypted between your phone and the node.
