### Documentation

- **`docs/ORG_SETUP.md` covers what a real macOS work-Mac setup ran into.** §1 shows a launchd
  LaunchAgent in place of PM2 (plist sketch, logs, restart). §2(a) shows signing the embedded
  node in interactively with `tags tag:org-chela` and no auth key, and lists three pitfalls:
  two Caddy processes on one `state_dir` ("Duplicate node key"), a `| tee`'d Caddy outliving
  its tmux window, and the Tailscale app signing the host back in. §2 adds the iPhone fix for
  "server can't be found" on `*.ts.net`. §3 adds a one-phone `hosts` IP-alias policy and its
  IP-reuse caveat. §4 adds Caddy `admin off`, the plugin's `webui false`, a 700/600 state dir
  and a Time Machine exclusion. §5 names tsnet's peerapi as an open LAN port. (CMX-472)
