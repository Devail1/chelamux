### Fixed

- **The Caddy example in `docs/ORG_SETUP.md` §2(a) now tells chela who approved.** It only
  stripped `Tailscale-User-Login` and never set it, so with
  `CHELA_DASHBOARD_IDENTITY_HEADER=tailscale` every dashboard approval was recorded as
  `dashboard`. The site block now runs caddy-tailscale's `tailscale_auth` and sets
  `Tailscale-User-Login` / `Tailscale-User-Name` from it (which also replaces any copy a
  client sent). It works for user-owned devices only, not tagged ones. The README now points
  to §2 for remote access. (CMX-468)
