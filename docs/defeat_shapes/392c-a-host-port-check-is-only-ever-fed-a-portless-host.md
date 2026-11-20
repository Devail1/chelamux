## 392c. A host:port check is only ever exercised against a portless host, so "same hostname" and "same origin" are indistinguishable

**Assertion form:** an Origin check (`urlparse(origin).netloc != request.host`) is proven by
rows that send a foreign hostname (`http://evil.example`), `null`, or nothing, and by positive
controls that send `Origin: http://localhost`. Every request goes through Flask's test client,
whose default `request.host` is a bare `localhost` with no port.

**Mutation that defeats it:** `urlparse(origin).hostname != request.host.split(":")[0]` (the
judge, CMX-392 round 1). With no port anywhere in the fixture, comparing hostnames is
byte-for-byte the same as comparing host:port, so the whole suite stayed green. In production
the dashboard serves on a port (5005), and a page on a *sibling port* of the same host
(another local service, an agent's own `python -m http.server`) is a different origin that the
mutated check accepts. The same blind spot hides `netloc in request.host` (a prefix origin),
`request.host.startswith(netloc)`, and stripping userinfo before comparing.

**Guard form that survives:** give the request host a port (`base_url="http://localhost:5005"`)
and feed the check the origins that sit one component away from it: a sibling port, the
hostname without the port, a prefix and a superstring of `host:port`, the same port on another
hostname, userinfo before `host:port`. Drop every other refusing signal from those rows
(no `Sec-Fetch-Site`, a valid cookie and token) so the Origin comparison is the only thing that
can refuse, and keep a positive control with `Origin == http://host:port` on the same ported
host. Drive it across every route that calls the check, and once over a real socket.

**The general rule:** a comparison over a structured value is only proven on the components
the fixture actually varies. If a component (port, scheme, userinfo) is constant or absent in
every test, any mutation that ignores that component is invisible.
