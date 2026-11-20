## 392d. A fix that varies one constant component of a structured value leaves the next one constant (the Origin's scheme, the token's length and case)

**Assertion form:** after #392c, the Origin check (`urlparse(origin).netloc != request.host`)
is driven with origins one component away from `host:port` — sibling port, prefix,
superstring, userinfo — and a positive control `Origin: http://localhost:5005`. Every row,
negative and positive, is `http://`. The session-token rows likewise vary the token's
*content* (wrong, another session's, truncated) but never its length upward or its case.

**Mutation that defeats it:** `origin.removeprefix("http://") != request.host`, or
`origin != f"{request.scheme}://{request.host}"` (CMX-392 round 2, verified by hand: both
stay green on the #392c suite). With the scheme constant, "parse the Origin" and "strip a
literal `http://`" are indistinguishable, and so are "compare host:port" and "compare the
whole origin including the scheme". In production the browser's Origin is `https://…`
(Caddy terminates TLS on the tailnet) while Flask sees plain http, so the second mutation
refuses the real Approve button. On the token, `sent.startswith(token)` survives too: no row
ever sends the real token *with* something appended.

**Guard form that survives:** vary the scheme in both directions — positive controls under
`http` AND `https` for the same `host:port`, and refusal rows (`https://` sibling port,
hostname without port, userinfo) so an `https` origin is not an accept-anything path. Decide
explicitly whether the scheme is part of the boundary and say so beside the check. For a
secret compared by equality, send a longer value (`token + "0"`) and a re-cased value
(`token.upper()`) as well as a shorter one.

**The general rule:** #392c's rule applies recursively. When a survivor is fixed by varying
one component, enumerate EVERY component of the value (scheme, host, port, userinfo, path for
a URL; length, case, prefix, suffix for a secret) and check which ones are still constant in
every row. The one you did not vary is where the next mutation lives.
