# Sandboxed shares — letting a guest type

A share (**⋯ → Share current session** on a pane) streams a live terminal, end-to-end
encrypted, to anyone holding the join link **and** the pairing code. By default a share is
**view only**: the guest watches, and nothing they type reaches your machine.

Letting a guest *type* into an ordinary window would hand them a real shell as you. So
chela only forwards guest keystrokes into a **sandboxed session** — a Claude Code session
running in a container that sees only one project directory — and checks that, live, on
the host, while the guest types.

## The three rules

Guest input (`T_INPUT`) is forwarded to the pane only when **all three** hold. The check
runs on the host, in `chela/collab_stream.py` (`Bridge._input_refusal`); the relay can't
read a frame, so it can't enforce anything.

1. **The `share_typing` setting is on.** Settings → Collaboration → *Guest typing*
   (env: `CHELA_SHARE_TYPING`). It's **off by default** and is read on every keystroke, so
   turning it off stops typing on live shares at once.
2. **The share was created with typing allowed.** The share dialog's default is
   *View only*. *Allow typing* can be picked only when rule 1 holds and the window
   verifies as a sandboxed session; otherwise the dialog shows why ("Typing is disabled
   in Settings", or "Not a sandboxed session — start one from New session → Sandboxed").
3. **The window verifies LIVE as a sandboxed session.**
   `share_sandbox.check_share_session(wid)` reads the pane's process tree from `/proc`
   (the launcher, exec'd by tmux directly with no shell between them, with only the docker
   client under it) and `docker inspect`s the container and its network. It re-runs at
   least every `SANDBOX_RECHECK_INTERVAL` (2 s) while the guest types. The result is never
   taken from when the share was created. Anything it can't verify counts as a failure:
   an unreadable `/proc`, docker being down, or a mount or capability it doesn't expect.

When input is dropped, the guest gets one "view only" notice, at most once every 10 s.

The share pill and the pane's *Share current session* row show **👁** for a view-only
share, **⌨** when typing is allowed, and **⚠** while the UNSANDBOXED override is on.

## Changing a live share's mode

You don't have to stop a share to change who can type. **Active shares** (the
**#btn-shares** pill, or *Share current session* on a window that is already shared)
lists each share with its mode as three buttons: **👁 View only**, **⌨ Allow typing** and,
where the override is offered, **⚠ Full access — UNSANDBOXED**. Each row also says what
that share allows. The link and pairing code stay the same, and a guest who already
joined keeps the connection, so nobody needs a new invite.

- **Down** (to View only) applies at once, with no confirmation. The next keystroke
  the guest sends is dropped.
- **Up** passes the same checks as creating a share with that mode
  (`app._access_gate`, used by both routes). Allow typing needs *Guest typing* on and a
  window that verifies as sandboxed; when it's refused, the row shows the same reason as
  the share dialog. UNSANDBOXED needs the typed window name. Its expiry and one-joiner
  binding start at the upgrade, so the first guest to type after it is the one bound.
- The server route is `POST /api/term/<wid>/share-mode` with `{"mode", "confirm"?}`.
  The bridge (`Bridge.set_mode`) applies the change in place.
- Every change writes a `share.mode_changed` event with `from`, `to` and `by`. An
  upgrade to UNSANDBOXED also writes `share.unsandboxed_granted`, and leaving it writes
  `share.unsandboxed_revoked`.
- The guest's status line says **"You can type now."** or **"View only now."**

Clicking *Share current session* on a window that's already shared never creates a new
share, because that would rotate the code. It opens Active shares with that share's row
highlighted, so you can change its mode there.

## Shares across restarts and deploys

A live share no longer ends when chela restarts (CMX-434).

**Where the share runs.** The bridge that streams a share runs in the **collab host**:
`chela collab`, a separate PM2 service (`chela-collab`, see
`examples/ecosystem.config.js`). The dashboard talks to it over an owner-only Unix
socket, `$CHELA_DIR/collab.sock` (mode 0600; the peer's uid is checked too). A dashboard
deploy doesn't touch a live share. `chela update` restarts `chela-collab` only when the
share code itself changed (`chela/collab_host.py`, `collab_stream.py`, `share_store.py`,
`e2e.py`, `share_sandbox.py`, `collab.py`). That holds on the nothing-to-pull path too:
if a bare `git pull` brought in share code after `chela-collab` started, `chela update`
finds the HEAD the service started on in the HEAD reflog and restarts it. When the
reflog can't say, it restarts it.

If `chela-collab` isn't running, the dashboard hosts the shares itself, as before. A
dashboard restart then interrupts them, and the next dashboard restores them. Only one
process hosts at a time: the holder of the `flock` on `$CHELA_DIR/collab.lock`. A
dashboard that hosts no share gives the lock back, so a waiting `chela-collab` takes over.

**What is kept.** Each live share is written to `$CHELA_DIR/shares.json`: the window,
its mode, the relay room, the pairing secret, the override's expiry and bound guest, and
the window's identity (tmux server pid, window id, pane pid). The file is mode 0600 and
is never written inside a git work tree. It holds the pairing secret, so treat it like the
code. Stopping a share removes it from the file. A process exit keeps it.

**What comes back.** When a host starts, each kept share is restored with the **same
link and pairing code**, so a guest reconnects by itself. These are not restored, and
`share.not_restored` in the event log says why:

- a share whose window is gone, or is no longer the same window (a tmux restart
  recycles `@N` ids);
- a **typing** share whose window doesn't verify as a sandboxed session now. Live, a pane
  that stops verifying ends the share, so a restart doesn't turn it into anything else;
- an **UNSANDBOXED** override that expired during the restart. The share comes back
  view only (`share.unsandboxed_expired`). An override with time left comes back with
  only that time left.

A restored typing share re-checks the sandbox before it forwards any input, as always.

**Two crypto details make a restore safe.** The pairing secret and the host's stream id
don't change, so a restored host restarting its sequence numbers at 0 would reuse AES-GCM
nonces. The host never seals a frame past a sequence ceiling that is already on disk, and
a restored host resumes at that ceiling. A restored host has also forgotten which guest
frames it has already seen, so the relay could replay an old keystroke. It sends a fresh
random `resume` challenge, sealed so only a paired guest can read it. It accepts input
only from a guest stream that answered it, and that answer also blocks every earlier frame
from the stream. A guest page that hasn't been updated can still watch a restored share
but can't type into it.

**What the guest sees.** On a restart the host sends `restarting`, not `ended`. The guest
page shows **host restarting…**, re-sends its hello with backoff (0.5 s, doubling to 8 s),
and goes back to live on the host's first frame. It calls the share ended only if the host
hasn't come back after 5 minutes. The page lives in the relay Worker, so this needs a
`wrangler deploy` of `chela/collab-relay`.

**Before a deploy.** `chela update` prints *"N live share(s) will be interrupted by
restarting …"* before a restart that takes down the process hosting them. For a hand
deploy, run `chela shares --restarting <services…>` first.

## Starting a sandboxed session

- Dashboard: **New session → Sandboxed session…**, then pick the project directory. It's
  in the sidebar's *New session* menu and in the phone's **+** menu.
- CLI: `chela share-session <project>`, where `<project>` is a path or a name under
  `CHELA_PROJECTS_DIR`.
- With web access (opt-in, per session): **New session → Sandboxed session · allow web
  access…**, or `chela share-session <project> --web`. See [Web mode](#web-mode-opt-in).

Both routes use `chela.share_sandbox`'s launcher, and both refuse to start without
docker, the image, the `claude` binary or a token file. They also refuse a workspace
that is `$HOME`, an ancestor of it, or inside a secrets directory (`~/.ssh`, `~/.claude`,
`~/.chela`, `~/.config`, …). A refusal never falls back to an unsandboxed launch.

Knobs, set in `chela.env`, which the launcher process reads:

| env | default | what |
|---|---|---|
| `CHELA_SHARE_SANDBOX_IMAGE` | `python:3.12-slim` | image for the guest and the proxy sidecar (must be pulled) |
| `CHELA_SHARE_SANDBOX_TOKEN_FILE` | Claude Code's `.credentials.json` | the token the proxy adds on egress, e.g. a `claude setup-token` token |
| `CHELA_SHARE_PROXY_UPSTREAM` | `https://api.anthropic.com` | the proxy's fixed upstream |
| `CHELA_SHARE_SANDBOX_WEB_IMAGE` | `chela-share-web:latest` | web mode only: the guest image with headless Chromium (build it, below) |
| `CHELA_SHARE_WEB_DENY` | unset | web mode: comma-separated domains (and their subdomains) always refused |
| `CHELA_SHARE_WEB_ALLOW` | unset (= any public host) | web mode: if set, ONLY these domains (and subdomains); IP literals are then refused |
| `CHELA_SHARE_WEB_HOST_RPS` | `1` | web mode: requests per second per site (burst 3) |
| `CHELA_SHARE_WEB_GLOBAL_RPS` | `8` | web mode: requests per second across all sites (burst 20) |
| `CHELA_SHARE_WEB_DENY_CIDRS` | unset | web mode: extra address ranges to refuse, on top of every private range and this host's own addresses |

### The token, and long-running shares

For a share that runs longer than a few hours, use a long-lived token:

```bash
claude setup-token            # prints a token valid for about a year
umask 077; printf '%s' '<the token>' > ~/.chela/share-sandbox-token
echo 'CHELA_SHARE_SANDBOX_TOKEN_FILE=~/.chela/share-sandbox-token' >> ~/.chela/chela.env
```

With the default, Claude Code's own `.credentials.json`, the session works, but it depends
on your host login. Claude Code refreshes that login every few hours. It writes a new file
in place of the old one, and the old token stops working. Here is how a session handles a
refresh:

- The launcher copies **only the access token** (never the refresh token) into a
  per-session directory, `~/.chela/share-token/<id>/`. It checks the source file every 2
  seconds and copies it again when it changes. The directory is removed when the session
  ends.
- The proxy sidecar mounts that **directory** read-only and reads the token from it on
  every request. It mounts a directory, not the file itself, because a mounted single
  file keeps showing the old file after a replace. The guest container mounts neither.
- If Anthropic rejects the token (401), the proxy reads the token once more and, if it
  changed, retries once with the new one.
- If the 401 persists, the proxy answers **502** with *"The host's Claude login expired —
  ask the operator to log in again on the host."* It never passes the 401 on, because a
  401 would start Claude Code's `/login` inside the guest, which can never work there.
  Log in again on the host (`claude`, then `/login`). The next request picks up the new
  token, with no restart needed.

## What is isolated

- The guest's Claude runs in a container. It sees the **workspace** (read-write, with
  `.env*` files masked and an existing `.git`/`.claude`/`.mcp.json`/`.envrc`/`.vscode`
  mounted **read-only**) and a read-only `claude` binary. It does not see `~/.ssh`,
  `~/.claude`, `~/.chela` or `~/.config`.
- The container runs as your uid with `--cap-drop ALL`, `no-new-privileges` and a
  read-only root. `/tmp` and `HOME` are tmpfs, and memory and pid counts are capped.
- Its only network is a per-session `--internal` bridge that gives the host **no
  address** (`inhibit_ipv4`), so it can't reach any host service or the internet. The
  only other thing on that bridge is a sidecar running `chela/share_proxy.py`, which
  forwards `/v1/…` to one fixed upstream and adds your token on the way out. **The token
  never enters the guest container.** (A session started with web access has exactly one
  more member on that bridge, the filtering egress proxy. See
  [Web mode](#web-mode-opt-in).)
- tmux starts the window's process directly, with no shell. When Claude exits, the
  container, the proxy, the network and the pane go with it.

A guest who can type can still spend your Claude usage through the proxy. That comes
with letting them drive Claude at all.

## Telegram: the session reaches your topic too

A sandboxed window gets a Telegram topic like any other agent window, and its replies are
relayed there. **Everything the session says reaches your topic** — including whatever the
guest pastes or has Claude read: a CV, a draft, a private file in the workspace. Inbound
works as usual too, so anything you type in that topic goes into the guest's session.

How it works: the session's transcript stays in the container's tmpfs and its hooks can't
reach the host, so the relay has no transcript to read. Instead, the credential proxy
parses each completed model turn from the response stream and appends the assistant's
visible text (with each tool call reduced to its name) to
`$CHELA_DIR/share-sessions/<session id>/outbox.jsonl`. That directory is mounted
read-write into the **proxy sidecar only**, never into the guest container, and a
workspace that would contain it is refused. `chela telegram` reads the outbox of any
window that verifies live as a sandboxed session, with the normal relay's formatting,
chunking and dedup. `chela doctor` reports such a window as healthy while its outbox
exists and keeps up with the proxy. It flags the window when the outbox is missing, or
when the proxy is forwarding turns that don't reach the outbox.

A turn is written only once its stream finishes. If the guest disconnects partway through
a reply, that reply never reaches the topic, even when the model finished it.

Outboxes stay on disk after the session ends, in directories readable only by you (mode
0700). A new sandboxed session removes any outbox older than 7 days.

**Turning the relay off for one session** (default on):

```bash
chela telegram --sandbox-relay @<wid>=off    # and =on to turn it back on
```

The setting is stored against the sandboxed session's id, not the window number, so it
doesn't carry over to whatever window later gets the same `@N`. While it's off, neither
the session's replies nor its pane prompts and status line are posted to the topic.

## Status: working/idle from the proxy

chela normally reads a window's status from Claude itself, and a sandboxed Claude is
invisible to that. Its proxy writes `activity.json` to the same per-session directory as
the outbox (proxy-only, never mounted into the guest). It records how many main-loop
requests are in flight and when the last one finished. For a window that verifies as
sandboxed, `chela peek` and the Wall then show **working** while a request is in flight
or finished less than 4 seconds ago (the gap while Claude runs a tool), and **idle** after
that. The pill's tooltip says "from the sandbox proxy". A permission prompt is not a
request, so the proxy can't see **waiting**. Ordinary windows are unchanged.

## Measured results (CMX-400, container route)

Measured by hand against a live sandboxed session:

| probe | result |
|---|---|
| `!` shell mode | blocked |
| Bash reading secrets / writing outside the project | blocked |
| the Read tool on the credentials file | not present |
| network, host services, the tmux socket | blocked (no-host-address network) |
| `.git` | read-only |
| workspace `.env` | masked |
| shift+tab | cycles manual → accept edits → plan → auto only, no bypass (re-measured 2026-10-01) |
| `/sandbox` | only its dependency tab (no bwrap in the container) |
| `/config` | affects only the container's temporary HOME |
| Ctrl+P Ctrl+Q, `/exit`, double Ctrl+C | each ends the pane with no shell; container and network removed |
| a real model call through the proxy | worked, with no token and no `sk-ant` string in the guest environment |

## Your checklist before turning typing on

Run these yourself on your own machine before you enable *Guest typing*. chela ships no
escape-probe scripts; this list is the test.

1. `docker image inspect python:3.12-slim` (or your `CHELA_SHARE_SANDBOX_IMAGE`) succeeds.
2. Start a sandboxed session on a **throwaway** project, then run
   `python -m chela.share_sandbox check @<wid>`. It prints `sandboxed`.
3. In that session, try `!cat ~/.ssh/id_ed25519`, `!ls ~`, and `!cat .env` (put a `.env`
   in the project first). Each fails or reads nothing.
4. Ask Claude to read `~/.claude/.credentials.json` and to write a file outside the
   project. Both are refused or fail.
5. Try `!curl -m5 https://example.com` and `!curl -m5 http://<your host's LAN IP>:<a
   port you serve>`. Both fail.
6. `!git commit --allow-empty -m x` fails, because `.git` is read-only.
7. Ask Claude for a trivial answer. It works, and `!env | grep -i -e token -e sk-ant`
   shows only the placeholder.
8. Exit with `/exit`. The pane closes, and `docker ps -a --filter label=dev.chela.share-sandbox`
   and `docker network ls | grep chela-share` are empty.
9. Share the sandboxed window with **Allow typing** to a second browser of your own, and
   type. Then swap the pane's process (for example `tmux respawn-pane -k -t <wid> bash`)
   and type again. Input stops within a couple of seconds, and the guest's view never
   shows the new shell: the share checks the pane before every frame it streams, drops
   the first frame from a process that isn't the sandbox, and ends with "the session
   stopped being a verified sandbox". (A view-only share of an ordinary window is not
   checked; it never claimed to show a sandbox.)

## Web mode (opt-in)

A sandboxed session normally reaches nothing but the token proxy. **Web mode** lets one
session read the public web: job listings, documentation, a public LinkedIn job page. It
is chosen per session at launch, and the default stays *no network*.

### What changes

- **The guest's network does not change.** The guest container is still alone on its
  per-session `--internal` bridge, the host still has no address on it (`inhibit_ipv4`),
  and there are still no published ports and no host sockets. The guest never gets a
  route of its own.
- **One more sidecar joins that network:** `chela/share_web_proxy.py`, a filtering HTTP(S)
  forward proxy, reachable as `chela-web:3128`. It is the only thing that can carry the
  guest's traffic out. It holds no credential, runs `--cap-drop ALL`, read-only, as your
  uid, and mounts only its own script (read-only) and its log file.
- **The guest gets proxy settings:** `HTTPS_PROXY` / `HTTP_PROXY` point at the sidecar,
  and `NO_PROXY` keeps Claude's own API calls on the token proxy. Claude's WebFetch,
  `curl`, `pip` and the browser all go through it. A tool that ignores the proxy has
  nowhere to go.
- **A browser in the guest:** the guest runs the `chela-share-web` image, which is the
  stock image plus a headless Chromium driven by Playwright, `curl`, `git` and a `browse`
  command. `browse <url> [--links] [--screenshot out.png]` prints a page's final URL,
  status, title and visible text. Claude can use it from Bash. There is no host browser
  and no host CDP port.
- **The pane says so:** the window is named `sandbox-web-N`, and its header shows
  **🌐 web**.

### What the egress proxy enforces

Each request goes through these checks in order:

1. **Ports 80 and 443 only.**
2. **The operator's lists**, if set: `CHELA_SHARE_WEB_DENY` always refuses its domains,
   and `CHELA_SHARE_WEB_ALLOW` (when set) admits only its domains. Unset means any
   public host. That's the default, because job listings live on many company sites.
3. **Public addresses only, checked after DNS.** The proxy resolves the name itself and
   refuses the request if **any** answer is not a public address. That covers loopback,
   RFC 1918, link-local (cloud metadata included), CGNAT `100.64/10`, IPv6 loopback,
   link-local and ULA, an IPv4 address hidden in a mapped, 6to4, Teredo or NAT64 IPv6
   address, multicast and reserved ranges. It also refuses the docker host gateway as the
   sidecar sees it, **this host's own interface addresses** (which matters on a host with
   a public IP), and `CHELA_SHARE_WEB_DENY_CIDRS`. It then connects to the **checked
   address**, never re-resolving the name, so there is no DNS-rebinding window.
4. **Rate limits:** each site (`www.linkedin.com` and `linkedin.com` count as one site)
   gets 1 request/s with a burst of 3, and all sites together get 8/s with a burst of 20.
   A request over the limit waits for its slot. If it would wait more than 30 s, it is
   refused with 429.
5. **A log line per request**, appended as JSON to
   `~/.chela/share-web/<session-id>.log` (under `CHELA_DIR`): time, method, host, port,
   the address it went to, the path (plain HTTP only), status, bytes each way, and the
   reason for any refusal. The log is kept after the session ends. The launcher prints
   its path in the pane when the session starts.

HTTPS is **tunnelled, not decrypted**. The log shows the host and the byte counts, but not
HTTPS paths. Decrypting would mean putting a CA inside the guest and letting the proxy read
the guest's traffic, and neither is worth it.

### Why Chromium runs with `--no-sandbox`

Chromium's own sandbox needs user namespaces or a setuid helper. Inside this container
that means adding `CAP_SYS_ADMIN` or loosening the seccomp profile. Either one weakens the
**outer** boundary (the container) to strengthen an inner one. So the browser runs
without its own sandbox. A renderer exploit would land in a container that already lets
the guest's Claude run arbitrary code, with the same workspace, the same network and the
same caps. It gains nothing the guest doesn't already have.

### Threat model: what web mode adds

The invariants are unchanged. A mistake, or a hostile page doing prompt injection, still
cannot reach the host, the LAN or any private address, still can't read outside the
workspace, and still can't see the token.

What web mode **adds** is a way **out**. A web page can tell the session's Claude to send
data to a public host it controls: in a URL, in a form post, or through `curl`.
Everything the session can read can leave this way, and **that is the whole workspace**.
So:

- **Keep only the guest's own material in the workspace.** Their CV, their notes, job
  descriptions. Nothing of yours, and no shared repo.
- Anything the guest pastes into the session, the session can also send out.
- The rate limits and the log are for noticing and slowing a runaway. They don't prevent
  a single small leak.
- Traffic leaves from **your** IP address. A site that blocks or flags automated access
  will see your address. That's why the limits are on by default.

### Building the browser image

The image is built locally and nothing is pulled from a chela registry. The Dockerfile
ships with chela at `chela/assets/share-sandbox-web/`. When the image is missing,
`chela share-session --web` prints the exact build command, which looks like this:

```bash
docker build -t chela-share-web <chela install>/chela/assets/share-sandbox-web
```

To use another tag, set `CHELA_SHARE_SANDBOX_WEB_IMAGE`. The image must keep `python`,
`browse` and Chromium. The proxy sidecars still use `CHELA_SHARE_SANDBOX_IMAGE`.

### What the live check verifies in web mode

`check_share_session` reads the mode from the launcher's own argv (`--net none|web`), so
the mode comes from the process tree and not from a flag chela stores. It then checks the
live session against that mode. Everything in [The three rules](#the-three-rules) still
applies. On top of that:

- the guest's mode label matches the launched mode;
- the guest's proxy variables are **exactly** the ones that mode sets. A `none` session
  with any `*_PROXY` variable fails;
- the session network's members are **exactly** the guest and the token proxy, plus the
  web proxy in web mode. A `none` session that gained a web sidecar or any other member
  fails, and so does a `none` session whose web sidecar is merely running;
- in web mode, the web sidecar is this session's, unprivileged, read-only and running
  chela's proxy script from a read-only mount, with no other mounts.

### Your checklist before switching a guest to web mode

Run this yourself, on a **throwaway** project, with a second browser of your own as the
guest. This list is the test.

1. Build the image (above). Then `chela share-session <throwaway> --web` starts a window
   named `sandbox-web-N`, and its pane header shows **🌐 web**.
2. `python -m chela.share_sandbox check @<wid>` prints `sandboxed`.
3. Run steps 3, 4 and 6 to 9 of the checklist above in this session. They must all
   behave the same way here.
4. **Public works:** `!curl -sI https://example.com` returns 200, and
   `!browse https://example.com` prints the page's title and text. A public LinkedIn job
   URL (`!browse https://www.linkedin.com/jobs/view/<id>`) loads, or LinkedIn's own login
   wall does.
5. **Private is refused, by IP and by name:** each of these gets a `403 refused` (curl
   may skip the proxy for `localhost` itself, in which case it fails outright):
   `!curl -m5 -x "$HTTPS_PROXY" http://127.0.0.1/`, `http://<your host's LAN IP>:<a port
   you serve>/`, `http://169.254.169.254/`, `http://172.17.0.1/` (or your docker gateway),
   `https://localhost/`, and a name that resolves to a private address (for example
   `http://<your router's hostname>/`, or a `nip.io` name like `http://10.0.0.1.nip.io/`).
6. **Other ports are refused:** `!curl -m5 https://example.com:8443/` gets 403.
7. **No route without the proxy:** `!curl -m5 --noproxy '*' https://example.com` fails,
   because the guest still has no route of its own.
8. **The rate limit holds:**
   `!for i in $(seq 10); do curl -s -o /dev/null -w '%{http_code}\n' https://example.com; done`
   finishes in roughly 7 s or more, not instantly.
9. **The log is there:** `tail ~/.chela/share-web/<session-id>.log` shows each request
   above, with host, status, bytes, and the refusals with their reasons.
10. **The mode can't drift:** start a plain (`none`) sandboxed session and run
    `docker network connect chela-share-net-<its id> chela-share-web-<a web session's id>`.
    `python -m chela.share_sandbox check @<wid>` on the `none` session now prints
    `NOT sandboxed`, and guest input stops within a couple of seconds.
11. `/exit` the web session. The pane closes, and
    `docker ps -a --filter label=dev.chela.share-sandbox` is empty, including the
    `chela-share-web-*` sidecar.

## The trusted-peer override (UNSANDBOXED)

For a very trusted peer, a single share can allow typing into a window that is **not**
a sandboxed session. That is a real shell on your machine. It's built to be hard to turn
on by accident, and to end on its own:

- It's offered only while *Guest typing* is on, and only on a window that isn't already
  sandboxed. It's never a default and never a global setting.
- The dialog labels it **"Full access — UNSANDBOXED: the guest can type into a real shell
  on this machine"**. To confirm, you type the **window name**, and the server checks
  that name against the live tmux window.
- It **expires** after `CHELA_SHARE_UNSANDBOXED_MINUTES` minutes (default **30**, range
  1–240). The share then goes back to view only by itself, and the guest is told.
- It's **bound to one joiner**: the first one to say hello (or type) after the grant.
  Anyone else on the same share stays view only. A guest who reloads the page gets a new
  stream id, so they're view only too; stop and re-share to re-pair. This binding keeps
  out other viewers. It doesn't protect against someone who holds the code and forges a
  stream id, because the code is the capability, so give it only to that peer.
- While it's active, a red **"UNSANDBOXED — guest can type"** banner shows on the pane
  header and in the share pill. The **#btn-shares** kill switch (Stop / Stop all)
  revokes it instantly.
- Every step goes to the event log. `share.unsandboxed_granted` records who, the window,
  the start and the expiry. `share.unsandboxed_expired` and `share.unsandboxed_revoked`
  record the end and its reason.
- Turning *Guest typing* off also disables an active override.
