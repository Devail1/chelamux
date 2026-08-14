## 29. An exact-output fixture whose payload is IDENTITY under the very transform it claims to guard

**Assertion form:** an exact-output test asserts a string produced by a transform function
(an escaping call, a level-pinning regex capture, a character-class alternative) — and the
fixture's *value* happens to be a fixed point of that transform: running the transform or
skipping it entirely produces the same output. The test's own doc comment may even name the
transform as the thing it guards, and the guard is not lying — it genuinely calls the
function it says it does. It just never gives that function anything to do.

**Mutation that defeats it:** delete or narrow the call (skip the escaping, pin the captured
level to whatever constant the fixture always uses, narrow a character class to the one
alternative the fixture always hits). The fixture's output is unchanged, because the
transform was a no-op on that particular input — the assertion cannot tell "the transform ran
and did nothing" apart from "the transform did not run."

**Guard form that survives:** for any assertion meant to pin a transform, pick a payload for
which the transform PROVABLY changes the output — a string containing the characters an
escaper actually escapes, a value other than whatever every other fixture in the file already
uses, a case exercising every alternative in a character class rather than just one. State
*why* the payload is diagnostic (which property of the input makes the row non-identity) so a
reviewer widening the suite later can check the claim against the code instead of re-deriving
it from scratch.

**Found:** CMX-279 rework round 5 (2026-08-14), PR #350. Three of `knMd`'s exact-output guards
were each built from a fixture that happened to be a fixed point of the branch it was meant to
pin: the round-4 fenced-code fixture (`const x = 1;`, `**not bold**`) has nothing for
`escHtml` to escape, so dropping the `escHtml` call inside the fence left the assertion
byte-identical; every heading fixture in the whole suite used `###`, so pinning the heading
level to the constant `3` (instead of reading `h[1].length`) passed; and the list-item regex
fixture only ever used `-` bullets, so narrowing `/^[-*]\s+(.*)$/` to `/^[-]\s+(.*)$/` passed
too. All three shipped clean through `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` (3076 passed,
0 failed). Closed by a single table-driven test (`KN_MD_BRANCH_TABLE` in
`tests/taskmodal_model.test.mjs`) enumerating every branch of `knMd` from the source, with each
row deliberately picked to be non-identity under whatever it guards — an HTML-special-character
payload inside the fence, one row per heading level 1 through 4, and both list-marker
characters — plus two negative-control rows (an unterminated fence, and an ol→ul list-kind
switch) for branches the round-5 finding did not name, to prove the table closes the space
rather than answering only the four findings asked for.
