## 392. A credential header is asserted by its key, in a fixture whose credential is already empty

**Assertion form:** a frontend guard proves a POST carries the page's session token with
`expect(Object.keys(headers)).toContain('X-Chela-Session-Token')`. The token itself is read
once, at import, from the page's boot block (`readSessionToken()` → `SESSION_TOKEN`). The test
file never rendered a boot block, so the module under test read `''` — the documented
"no token" fallback — and the header the test saw was `{'X-Chela-Session-Token': ''}`.

**Mutation that defeats it:** blank the value at the call site,
`headers: decideHeaders()` → `headers: { ...decideHeaders(), 'X-Chela-Session-Token': '' }`.
The key is still there, so the key check passes; and the value it overwrote was *already* `''`
in the fixture, so even a value check against the module's own `SESSION_TOKEN` would pass.
The corruption is invisible twice over: the assertion looks at the wrong half of the pair, and
the fixture parks the right half on the very value the mutation writes. In production the
server (`chela/dashboard/humanproof.py`) refuses every decision with an empty token, so the
operator's Merge / Approve buttons would all 403.

**Guard form that survives:** seed a distinctive, non-empty token into the boot block BEFORE
the module reads it (`vi.hoisted(() => { document.body.innerHTML = '<script id="chela-boot">
{"sessionToken": "tok-…"}</script>'; … })`, since static imports run before any test body),
then assert the header's VALUE equals that seeded token:
`expect(headers['X-Chela-Session-Token']).toBe(TOKEN)`. Any call site that drops, blanks or
replaces the token now differs from a value only the fixture could have produced. Pin it at
EVERY call site that sends it (here the card's Merge, the approvals approve AND deny, the
access-request decision), not only at the shared `decideHeaders()` helper: the mutation lands
at the call site, where a helper-level test cannot see it.

**The general rule:** a check on a credential, ID or secret must compare against a value the
fixture chose and that no fallback produces. If the fixture leaves it at the fallback (`''`,
`null`, `0`, a default), then "the fallback leaked through" and "the real value was sent" are
the same observation.
