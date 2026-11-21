### Added

- **The dashboard installs as a home-screen app.** The page now links an `apple-touch-icon`
  (180×180) and a `manifest.webmanifest` (192, 512 and a maskable 512 PNG,
  `display: standalone`), with `theme-color` and the iOS web-app tags, so "Add to Home Screen"
  saves the chela mark instead of a page screenshot and opens without browser chrome. The
  PNGs are drawn from the chela mark itself, with no new dependency.
- **A `work` look for a second dashboard.** `CHELA_DASHBOARD_ICON_VARIANT=work` adds a
  briefcase badge to the icon's corner (a shape difference, not only a colour one) and a
  different `theme-color`; `CHELA_DASHBOARD_APP_NAME` sets the label under the icon and the
  page title (default `chela`). Both are env-only; an unknown variant is refused (503) rather
  than read as the default. See `docs/ORG_SETUP.md`. (CMX-457)
