### Security

- **Share-request decisions and the card's Merge button now need the operator's browser
  session too.** Approve, Deny and Revoke on an access request, and the card's Merge (which
  runs `gh pr merge` itself), used to act on any POST that reached the dashboard over
  loopback, so a dispatched agent could merge its own PR or grant a guest a mount with one
  HTTP call. They now need the same proof as an override decision: the session cookie, the
  page's token and an `Origin` naming the dashboard's own host and port. If any is missing or
  wrong, the dashboard returns 403 and does nothing. The access-request sheet and the card's
  buttons send the token, so they work as before.
