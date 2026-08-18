## 68. A sibling regex's flag or capture bound drifts from the regex it must mirror, invisible to a single-shaped fixture family

**Assertion form:** two regexes are meant to agree on one axis — a case-insensitivity flag,
a capture bound — because the second regex only ever fires on input the first regex already
accepted (a "does this bullet park" regex and a "what's its reason" regex; a "does this line
open a block" regex and a "where does the block's payload end" regex). The exact-output test
for the second regex passes a fixture that happens to be a fixed point of the axis in
question — the *only* marker on the line, so a non-greedy and a greedy capture agree; a
lowercase marker, so a case-fold and a literal match agree — and, worse, every OTHER fixture
in the file shares that same shape, so there is no sibling test to notice either.

**Mutation that defeats it:** drop the flag from the second regex (case-fold silently stops
matching what the first regex still accepts), or widen its capture bound (`(.*?)` → `(.*)`).
Every fixture in the file is a fixed point of the change — single marker per line, lowercase
marker — so the whole suite stays green. The value at risk isn't "does this get parked" (the
first regex, untouched, still decides that correctly) — it's the payload the second regex
was supposed to extract, which silently degrades to `None` or picks up garbage from whatever
follows on the line.

**Guard form that survives:** for a regex whose scope must mirror a sibling's, add one
fixture that is diagnostic on that *specific* axis rather than reusing the file's existing
shape — a marker in the case the sibling's flag alone accepts, and a line carrying a SECOND,
unrelated marker after the first so a greedy capture visibly swallows past its own closing
bound. State in the comment which axis the fixture is pinned to, so a reviewer can check "is
this actually a fixed point of the mutation" against the regex, not just re-run the suite.

**Found:** CMX-298 round 7 (2026-08-16), PR #372. `BLOCKED_REASON_RE` deliberately mirrors
`BLOCKED_RE`'s `re.IGNORECASE` (a bullet parked by the first regex must not lose its reason
just because the marker was written uppercase) and uses a non-greedy `(.*?)` so the reason
stops at its OWN marker's `-->` rather than swallowing a second marker on the same line (a
depends: pairing this repo's own `_TRAILING_COMMENT_RE` comment documents as routine). Every
fixture in `tests/test_markdown_parked.py` wrote its marker lowercase and alone on the line,
so both `re.IGNORECASE` and the non-greedy bound could be dropped independently and the suite
stayed green (`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`, 3171 passed, 0 failed, both times).
Closed by two fixtures, each diagnostic on one axis: an uppercase `<!-- BLOCKED: ... -->`
marker, and a bullet carrying both a `blocked:` and a `depends:` marker on the same line.

⚠️ Related but distinct from shape 29: 29 is one fixture shape reused as a fixed point across
several DIFFERENT branches of one function; here it's the whole FILE's fixture family sharing
one shape that happens to be a fixed point of a SIBLING regex's own flag or bound.
