## 30. A branch-enumeration table proves the dispatch fired, not that the transforms nested inside it ran

**Assertion form:** a table enumerates every branch of a dispatcher function, one row per
entry condition (does this `if` fire, does it emit the right tag) — the correct response to
shape #24, and each row's *own* payload is deliberately non-identity for the thing that row
was written to pin. But a branch often does more than open a tag: it also calls a shared
helper partway through its body (an inline-rendering/escaping function, a state-closing
function like "close whatever was open before this new thing starts"). The table's per-row
payload was chosen to make the *branch's own* transform non-identity and nothing checked
whether it was ALSO non-identity for every helper nested inside that branch, or whether the
state the helper is supposed to clean up was actually dirty when that row's fixture ran.

**Mutation that defeats it:** drop or dead-code the nested helper call inside a branch whose
row payload happens to be a fixed point of that helper (plain alphanumeric text is identity
under an inline-markdown/escaping call), or whose row payload never puts the branch in the
state the helper exists to clean up (no list open when the row's fixture reaches that
branch). The row's own assertion — pinned to the branch's own tag — is unaffected, because
the corruption is one level below what that row was checking.

**Guard form that survives:** treat the table as enumerating a cross-product, not a single
axis — for every branch that calls a shared inline-rendering helper, include a row whose
payload is non-identity under THAT helper too (not just under the branch's own dispatch); for
every call site of a shared state-closing helper, include a row that actually has the state
open immediately before that branch fires, with no intervening blank line or other
state-clearing branch. Count the helper's call sites from the source (not from what the
current fixtures happen to reach) and check each one off explicitly.

**Found:** CMX-279 rework round 6 (2026-08-14), PR #350, recurring one level below shape #24
inside the very table shape #24 was closed by. The round-5 `KN_MD_BRANCH_TABLE` gave its
blockquote row (`> quoted`) and its four heading rows (`# h1` … `#### h4`) plain-text
payloads — non-identity for the branch's own dispatch (does it emit `<blockquote>`/`<hN>`),
but identity under `knInline`, the helper both branches call to render their content. Dropping
`knInline` from either branch left both rows byte-identical, even though the same table had
already fixed this precise defect one branch over, for the fenced-code row. Separately,
`closeList()` — the helper that closes a still-open `<ul>`/`<ol>` before a new block starts —
has eight call sites in `knMd`; the round-5 table's rows exercised six (heading, blank line,
both list-kind switches, the fence, and the EOF tail) but no row put a list open immediately
before a blockquote or a plain paragraph line, so dead-coding either of those two call sites
left every existing row's assertion unchanged. All four survived
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3080 passed, 0 failed) in the judge's own
mutation checkout. Closed by six more rows in the same `KN_MD_BRANCH_TABLE`: a blockquote and
a heading row each carrying a bold span *and* HTML-special characters (non-identity under
`knInline`), and a blockquote and a plain paragraph line each placed directly after an open
`-` run with no blank line between (state dirty when the branch fires, so a dead-coded
`closeList()` nests the new block inside the list's last `<li>` instead of closing it first).
