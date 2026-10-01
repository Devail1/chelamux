## 416b. The host test proves a reason is sent; nothing proves the client shows it

**Assertion form:** a server ends a session and sends the client a payload with a
human-readable reason (`{"t":"ended","reason":…}`). The server-side guard captures the
outgoing payload and asserts the reason is in it. The client page has a matching change, to
render "ended — <reason>" instead of a bare "ended", but the page is an inline module script
that needs WebCrypto and a live socket, so no test loads it.

**Mutation that defeats it:** make the client ignore the reason
(`'This share has ended — ' + m.reason… : 'This share has ended.'` becomes
`'This share has ended.' : 'This share has ended.'`). Every guard stays green. The reason is
still sent, and the server test only ever looked at what was sent. The user-visible half of
the feature is gone. (CMX-416, PR #567.)

**Guard form that survives:** test the rendering half against the shipped source. When the
page cannot be booted, lift the real handler statement out of the HTML with a pattern and run
it with a stub for the display function (`new Function('m', 'showEnded', stmt)`). Assert the
exact rendered string for a real reason, a capped long reason, and the no-reason fallback.
Read the reason from the server's own constant instead of retyping it, so the two halves
cannot drift apart. Make the extractor assert that it found the statement, so a renamed
handler fails loudly instead of passing vacuously.

**Why this is distinct from [[50|shape 50]]:** shape 50 proves two halves of one surface
separately but never runs them as a chain. Here only one half is proven at all. The wire
payload is the boundary, and each side of it needs its own guard.
