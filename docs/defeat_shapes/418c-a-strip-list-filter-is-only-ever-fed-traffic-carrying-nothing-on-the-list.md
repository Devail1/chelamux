## 418c. A strip-list filter is only ever fed traffic carrying nothing on the list

**Assertion form:** a relay drops a named set of items on the way through — the egress
proxy never forwards hop-by-hop headers (`Keep-Alive`, `Proxy-Authenticate`, `Upgrade`,
`Trailer`, `TE`, …) from the upstream response to the guest, nor `Proxy-Authorization` from
the guest to upstream. The end-to-end tests drive a real proxy against a real loopback
backend and assert the body and status arrive intact. But the test backend only ever sends
`content-length` and `connection`, and the test client only ever sends `Host` — so no item
on the strip list is ever in the traffic. The happy path is fully proven; the filter has
nothing to filter.

**Mutation that defeats it:** delete the strip-list clause (`if k.lower() not in _HOP and
k.lower() != "content-length"` → `if k.lower() != "content-length"`). Every header is now
relayed verbatim, including the upstream's `Proxy-Authenticate` and a guest's
`Proxy-Authorization`, and the suite stays green because no fixture carried one.

The same shape hit two siblings in the same round: the `deny_nets` loop over the IPv4
embedded in an IPv6 address was only fed `::ffff:<private>` addresses, which the
`is_global` check refuses first (see 337), and the host-name syntax check was only fed names
the stub resolver could not resolve anyway.

**Guard form that survives:** make the fixture EMIT every member of the strip list (one
backend path that sends each hop-by-hop header plus one end-to-end header such as `X-Job`,
and one that echoes back the request headers it received), then assert the end-to-end
header IS relayed and none of the stripped ones are, in both directions. For a filter that
sits behind an earlier check, pick inputs only the guarded check can refuse (a GLOBAL
`::ffff:1.2.3.4` with `1.2.3.4` denied; a resolver that answers ANY name with a public
address, so only the syntax check can refuse a malformed one), and pair each with an
accepted control.
