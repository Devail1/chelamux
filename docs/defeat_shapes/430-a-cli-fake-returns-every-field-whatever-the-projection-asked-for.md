## 430. A CLI fake returns every field whatever the projection asked for, so dropping a requested field is invisible

**Assertion form:** code shells out to a CLI that PROJECTS its output onto the fields named
in an argument (`gh issue list --json number,title,url,state`), and the test fakes that CLI
with a canned payload — a fixture builder that always emits the full record (`number`,
`title`, `url`, `state`, `labels`, …). The test then asserts the parsed result
(`{id: "open", other: "closed"}`).

**Why it doesn't guard:** the real `gh` returns ONLY the fields its `--json` requested; the
fake ignores the argument and hands back everything. So the field list in the argv — the one
thing that decides whether the real call ever sees `state` — is never exercised. Every
assertion on the parsed result passes off data the real CLI would not have returned.

**Mutation that defeats it:** (CMX-430) `"--json", "number,title,url,state"` →
`"--json", "number,title,url"`. In production every requested issue then arrives
state-less, is unclassifiable, and the id refresh fails on every tick (nothing ever
reconciles); the suite stays green because the canned payload still carries `state`.

**Guard form that survives:** make the fake honour the projection — parse the requested
field list out of the argv it was called with and return each record filtered to exactly
those fields (`{f: rec[f] for f in requested}`), then assert the parsed result. Dropping a
field the code needs now starves the parser exactly as the real CLI would. (Asserting the
argv string literally also goes red, but pins the spelling rather than the consequence; a
reordered or re-spelt list that still asks for `state` should stay green.)

**Related:** [[24|shape 24]] (a subprocess fake dispatches on an argv prefix, so the rest of
the argv is never inspected) — the same blind spot, on the output side instead of the routing.
