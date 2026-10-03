### Added

- **A sandboxed guest can request more access, and the operator approves or denies it.**
  Inside a sandboxed session, the guest or its Claude runs
  `python3 ~/bin/chela-request mount|domain|operation <target> --why "…"`. The request goes
  to a write-only route on the session's token proxy and lands in a host-only file. It does
  nothing by itself, and the guest can't read it back or see the decision.
  - The operator sees each request in the dashboard (the **🙋 N access requests** pill),
    as an event in the log, and as a push when `CHELA_NOTIFY_URL` is set (ntfy or
    Telegram). They can also run `chela share-requests` in a terminal. Approvals last
    60 min by default.
  - Mounts are read-only unless the guest asked for write and the operator ticks
    *allow write*.
  - An approved mount is applied by **re-launching** the guest container with it at
    `/extra/<name>`. A running container is never widened. Expiry or revocation relaunches
    the container without the mount.
  - The secrets directories (`~/.ssh`, `~/.claude`, `~/.chela`, `~/.config`, `~/.secrets`,
    …), `$HOME` and its ancestors, the tmux socket directory, system directories and
    `/mnt/*` are refused even when approved, and so is a symlink into any of them. The
    refusal is checked at approval, at launch and by the live sandbox check, and the UI
    shows why.
  - Domain approvals extend a web session's `CHELA_SHARE_WEB_ALLOW` list. Operations are
    only recorded.
  - Every request, approval, denial, refusal, expiry and revocation is a
    `share.request_*` event. Stopping the share or turning *Guest typing* off revokes
    every approval.
  - Nothing approves automatically, and chela's merge-gate hook denies
    `chela share-requests approve` and the approve route to Claude sessions. (CMX-7)
