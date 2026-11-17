### Security

- **A dashboard approval must now come from the operator's browser, not just from
  loopback.** An agent on the same machine could approve its own `chela merge --override`
  request, and so merge its own always-human PR, with one raw HTTP POST that had no Origin
  header. Approving or denying an override or release, deciding a sandboxed guest's access
  request, and a Work card's Merge now need the page's session: an `HttpOnly` cookie, the
  token the page embeds, and an Origin naming the dashboard. Anything else is refused with a
  403 and nothing is decided. `chela.overrides.decide` also stops treating the `dashboard`
  channel as proof by name. Telegram approval is unchanged. A dashboard tab opened before the
  upgrade (or before a dashboard restart) must be reloaded once before it can decide.
  `docs/TRUST_AND_APPROVAL_POSTURE.md` lists what this does not stop and which other routes
  should get the same proof. (CMX-392)
