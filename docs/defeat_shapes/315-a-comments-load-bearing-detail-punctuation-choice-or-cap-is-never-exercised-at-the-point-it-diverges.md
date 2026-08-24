## 315. A comment's load-bearing detail (a punctuation choice, a numeric cap) is never exercised at the one point where the naive alternative would diverge

**Assertion form:** a comment or docstring explains WHY a specific implementation choice was
made instead of an equally plausible-looking alternative — curly quotes instead of `"`
because straight quotes are a shell metacharacter the downstream sanitizer strips to a space;
a hard `[:N]` slice instead of the raw string because the value is unbounded, agent-authored
text about to be persisted. Every test that exercises the surrounding feature asserts the
*substance* survives (the excerpt text appears in the notice; the message appears in the
payload) but none of them is built to fail if the specific mechanism the comment argues for
were swapped for the alternative it explicitly warns against — because no fixture ever reaches
the place the two choices produce different output.

**Mutation that defeats it:**
- Swap the argued-for character for its plain-ASCII look-alike: `f" Said: “{...}”"` →
  `f' Said: "{...}"'`. Every existing test's `said` fixture (`"Fixed the parser and added 3
  tests, all green"`, `"line one\nline two"`, `"w" * 5000`, a 166-char sentence) only ever
  asserts that the excerpt's *text* is present or absent — never that a quote character
  brackets it — so a straight-quoted frame passes every assertion identically to a
  curly-quoted one, even though `sanitize_prompt`'s `SHELL_META_RE` (which every one of those
  same tests routes through) strips `"` to a space and the delimiter silently disappears from
  the real pushed line.
- Drop the cap: `payload["final_message"] = said[:FINAL_MESSAGE_PAYLOAD_CHARS]` →
  `payload["final_message"] = said`. The one existing payload test's fixture (`"detail " *
  40"`, 280 chars) sits comfortably under `FINAL_MESSAGE_PAYLOAD_CHARS` (4000), so slicing at
  4000 and not slicing at all produce byte-identical output for that fixture — the assertion
  `payload["final_message"] == long_text` passes either way, and the cap is provably
  unexercised.

**Why this is distinct from [[28|shape 28]] and [[312|shape 312]]:** shape 28 is a fix closed
narrower than a note's own prescription — the gap is *known and named* but only partly acted
on. Shape 312 is a default that every call site happens to override explicitly, so the default
itself is never read. Here nothing is missing from the fixture list in an obviously nameable
way — the feature has real, passing coverage of its headline behavior — the gap is that the
comment names a *reason* for a specific choice, and that reason is a claim about behavior at a
boundary (a sanitizer pass, a length cap) that no fixture happens to sit on. The tests read as
thorough because they cover the feature; they just never cover the boundary the comment is
actually defending.

**Guard form that survives:** when a comment explains "we chose X over Y because Z", write the
test that specifically exercises Z, not just a test that X's overall output looks right:
- For a delimiter/framing choice defended against a specific downstream transform, assert the
  delimiter characters themselves survive that transform in the final output (`f"“{said}”" in
  text`), not just that `said`'s words appear somewhere in it.
- For a numeric cap defended as "keeps a persisted value bounded", pick a fixture that is
  provably *longer* than the cap and assert the stored value equals the input sliced at the
  cap — a fixture sized anywhere at or under the cap cannot distinguish "capped" from
  "uncapped" no matter how carefully its assertion is worded.

**Found:** `chela/inbox.py`'s `_line`/`final_message` payload write (CMX-318 rework round 3,
PR #396). `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3344 passed) under both
mutations above, applied independently, in a throwaway checkout of the PR head. Closed by
`test_the_said_excerpt_stays_curly_quoted_through_sanitization` (asserts `f"“{said}”" in text`
on the real pushed line, so a straight-quoted frame goes red the moment `SHELL_META_RE` eats
its delimiters) and `test_the_payload_final_message_is_capped_at_the_payload_limit` (a
7000-char fixture, asserting the stored payload equals the input sliced at
`FINAL_MESSAGE_PAYLOAD_CHARS`, so dropping the slice goes red).

**See also:** [[318|shape 318]] — a different gap found on the same task, same PR: that one is
about a negative control's *fixture shape* drifting from the sibling it claims to mirror; this
one is about a comment's claimed mechanism never being independently exercised at all.
