### Added

- **The dashboard can name who did something.** Set `CHELA_DASHBOARD_IDENTITY_HEADER` to
  `tailscale` (reads the login `tailscale serve` sets) or `cloudflare-access` (verifies the
  Access JWT: signature, `aud`, `iss`, `exp`). The header counts only from a loopback peer; from
  any other address the request is refused and logged. Every approving or mutating action then
  records a `dashboard.action` event with `by`, an override reads "approved by you@… via
  dashboard", and the sidebar head shows who you are signed in as. Off by default. Tailscale mode
  is attribution, not authentication: a local process can send the same header. See
  `docs/DASHBOARD.md`, "Who did it". (CMX-385)
