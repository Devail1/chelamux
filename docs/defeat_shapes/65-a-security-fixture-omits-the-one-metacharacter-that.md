## 65. A security fixture omits the one metacharacter that would tell two escaping helpers apart, so the weaker one still passes

**Assertion form:** a value is rendered into two different HTML contexts from the same source
string — free text between tags (`<span>${escHtml(f.path)}</span>`) and an attribute value
(`data-diff-file="${attrEsc(f.path)}"`) — using two DIFFERENT escaping helpers, because the two
contexts have different escaping requirements (`attrEsc` = `escHtml` plus `"` → `&quot;`, per
`util.js`'s own comment: "escHtml … does NOT escape quote characters that would break out of an
attribute value"). One shared "evil path" fixture (`'<img src=x onerror=alert(1)>.js'`) is used
to prove BOTH call sites are escaped, asserting `querySelector('img') === null` for the
tag-breaking half. The fixture contains the metacharacters that break out of a TEXT node
(`<`, `>`) but not the one that breaks out of an ATTRIBUTE value (`"`).

**Mutation that defeats it:** swap the attribute call site's `attrEsc(f.path)` for
`escHtml(f.path)`. `escHtml` still escapes `<img …>` into inert text, so the fixture's `<`/`>`
characters are neutralized in the attribute exactly as they were in the text node — the
`querySelector('img')` assertion (aimed at the text-node half) stays green either way, because
it was never reading the attribute-rendered half at all. The one metacharacter that would make
the two helpers diverge (`"`) is simply absent from the fixture, so both the correct and the
broken code produce the same observable result for every input the suite is capable of
constructing.

**Why this is distinct from [[47|shape 47]]:** shape 47 is a single early-return filter whose
one accepted input value never varies. This shape is two DIFFERENT call sites sharing ONE
fixture value that happens to only exercise the subset of escaping behavior the two call sites'
helpers agree on — the divergence isn't in a filter's branch, it's in which characters two
sibling helpers each choose to escape, and the fixture never contains the one character where
they part ways.

**Guard form that survives:** when a value reaches two escaping call sites through different
helpers, build one fixture containing every metacharacter that distinguishes them (not just the
ones the "primary" mutation — e.g. dropping escaping entirely — would need), then assert each
call site's own rendered/parsed-back value independently: the text node's `.textContent` and the
attribute's own property (`el.dataset.diffFile`, not just "no `<img>` appeared anywhere in the
row"). A path containing `"` is legal on Linux and can come straight out of `git ls-files
--others` in a real worktree, so it's not a contrived input — it's the exact shape of value the
gap would fail on in production.

**Found:** CMX-299 rework round 4 (2026-08-16), judge review of PR #373.
`chela/dashboard/static/js/diffpanel.js`'s `_fileListHtml` renders a changed file's path into
`.diff-file-path`'s text (via `escHtml`) and into the row's `data-diff-file` attribute (via
`attrEsc`) — the value the click handler reads back and sends to `/diff/patch`.
`tests/diff_modal_wiring.test.mjs`'s XSS test used one evil path with no `"` in it, so
`attrEsc(f.path)` → `escHtml(f.path)` at the attribute call site left every assertion green:
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3183 tests) stayed green with the mutation in
place. Closed by adding `"` to the evil-path fixture and asserting `row.dataset.diffFile ===
evilPath` — under the mutation, the unescaped `"` breaks the attribute open early and the
parsed-back value comes back truncated instead of matching.
