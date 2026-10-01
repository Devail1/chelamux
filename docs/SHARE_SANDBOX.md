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

## Starting a sandboxed session

- Dashboard: **New session → Sandboxed session…**, then pick the project directory. It's
  in the sidebar's *New session* menu and in the phone's **+** menu.
- CLI: `chela share-session <project>`, where `<project>` is a path or a name under
  `CHELA_PROJECTS_DIR`.

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
  never enters the guest container.**
- tmux starts the window's process directly, with no shell. When Claude exits, the
  container, the proxy, the network and the pane go with it.

A guest who can type can still spend your Claude usage through the proxy. That comes
with letting them drive Claude at all.

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
| shift+tab | cycles manual → plan → auto only, no bypass |
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
