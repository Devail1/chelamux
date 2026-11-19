### Security

- **An override decision on the dashboard must now prove the operator's browser session.**
  Before, a dispatched agent could approve its own override with a plain HTTP POST from
  loopback: a request with no `Origin` header passed the same-origin check, and
  `overrides.decide` trusted any `channel="dashboard"` caller by name. Now `/` and the
  `/override/<id>` confirm page set an `HttpOnly`, `SameSite=Lax` session cookie and embed a
  token, an HMAC-SHA256 of the session id under a key that exists only in the dashboard
  process's memory and changes on every restart. Approve and Deny, from the approvals sheet
  or the confirm page, need the cookie, the matching token and an `Origin` that names the
  dashboard's own host and port (`http` or `https`). If any is missing or wrong, the
  dashboard returns 403 and decides nothing. A `channel="dashboard"` call to
  `overrides.decide` without `browser_proven=True` is refused. Telegram is unchanged.
