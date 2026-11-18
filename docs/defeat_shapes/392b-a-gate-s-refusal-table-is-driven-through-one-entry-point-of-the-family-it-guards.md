## 392b. A gate's refusal table is driven through ONE entry point of the family it guards, with each check fed only its easiest refusing input

**Assertion form:** a shared check (`humanproof.refusal`: Origin, Sec-Fetch-Site, session
cookie, token) is proven by a thorough-looking table: one row per missing proof, each refused
403 for its own reason, plus a positive control. Every row drives the SAME route
(`/api/override/<id>/approve`). The sibling routes that call the check (the confirm page's
form POST, deny, the access-request approve/deny/revoke, the card's Merge) each get one
"no proof at all" request, refused, and nothing else.

**Mutations that defeat it** (each green against the old suite, hand-run 2026-11-18):

- *a sibling narrows its own call* — the confirm page refuses only
  `decision == "approve"`, so an agent's raw form POST can still DENY the operator's request;
  the one sibling test sent an approve;
- *a sibling refuses everyone* — `if why or True:` on the card's Merge is a broken button, and
  a refusal-only test of that route is green by construction;
- *a check loosened at its boundary* — `Sec-Fetch-Site` also accepting `same-site` (a sibling
  port on localhost), `if origin is None` instead of `if not origin` (an empty Origin), a
  prefix match instead of `compare_digest` (a truncated token): each row fed only the
  textbook refusing input (`cross-site`, no header, `"0" * 64`), never the input that sits
  just across the boundary the check draws;
- *the page rotates the session* — `/` minting a fresh session id on every load instead of
  reusing the cookie: one page load then one POST still works, but a second tab (the
  notification's confirm page) strands the first tab's token. No test loaded two pages.

**Guard form that survives:** drive EVERY entry point through EVERY row (a
route × shape matrix, the side effect recorded, never reached) AND give every entry point its
own positive control that reaches the side effect exactly once. Add the boundary inputs as
rows: the value one step inside the allow-list, the empty string beside the missing header, a
prefix of the right token. For anything a page issues (a session, a token), load the page
twice in one client and assert the second load leaves the first one's credential working.

**The general rule:** a table proves the check *on the entry point it drives*, for *the
inputs it feeds*. A shared guard is only as strong as its weakest caller and its loosest
comparison, so the matrix has to span callers × boundary inputs, and every caller needs a
positive control or "refuses everyone" looks exactly like "refuses the attacker".
