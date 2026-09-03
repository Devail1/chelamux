## 338. A new pattern added to an enumeration-tested list with no matching probe

**Assertion form:** a guard is a list of patterns matched against a parallel, hand-maintained
list of real-world probes (one test per pattern-family, driven through the actual matcher —
here `git check-ignore` against a `.gitignore` glob list). The probe list's own comment says to
keep it "the full list, not a representative sample" — an explicit convention that every
pattern earns its keep by having a probe only it can satisfy. A later change adds a NEW pattern
to the list but adds no new probe, because every filename already in the probe list happens to
be caught by one of the OTHER, pre-existing patterns. The new pattern is syntactically valid
and can even be individually motivated (a real scratch file it was written to catch), but the
suite has no way to notice if that pattern stops matching anything at all — it was never the
thing making any assertion pass.

**Mutation that defeats it:** corrupt the new pattern into a still-valid-looking sibling that
matches nothing real (e.g. append a suffix — `*selfcheck*.json` → `*selfcheckDISABLED*.json`).
Every existing probe in the parallel list is matched by a different, untouched pattern, so
every existing assertion still passes; nothing in the suite ever asked "does *this specific*
pattern still match *its own* motivating filename."

**Why the existing probes don't catch it:** the probe list and the pattern list are two
separate hand-maintained collections kept in sync only by discipline, not by construction —
adding a line to one does not require touching the other, and nothing enforces a bijection
between them. A probe list can look complete (every existing pattern has at least one probe
that happens to match it) while a specific new pattern has zero probes that depend on it
exclusively, and that gap produces no visible symptom until someone deliberately checks which
pattern is doing the matching for each probe.

**Guard form that survives:** when adding a pattern to an enumeration-tested list, add a probe
filename to the parallel list that ONLY the new pattern matches — not one already covered by
an existing pattern — so removing or narrowing the new pattern in isolation turns that probe
red. If the pattern was motivated by a real file (a scratch artifact, a generated filename),
that real filename is usually the correct probe to add — it is evidence the pattern is
load-bearing for something that actually happened, not a synthetic string invented to exercise
the glob.

**Found:** `.gitignore`'s self-check scratch-file patterns (CMX-337, PR #434, round 5). Round 4
committed a stray round-3 self-check artifact, `.chela_selfcheck_cmx337_round3.json`, and (in
an earlier commit on the same branch) added `*selfcheck*.json` to `.gitignore` to cover its
no-separator spelling — but `tests/test_gitignore_scratch_files.py`'s
`REAL_SELF_CHECK_SCRATCH_FILENAMES` list, whose own comment says to keep it "the full list, not
a representative sample," was never extended. All five existing probes are matched by
`.chela-self-check-*.json`, `*self[-_]check*.json`, or `*scratch*experiment*.json`; none of them
touch `*selfcheck*.json` at all. The judge mutated the new pattern to
`*selfcheckDISABLED*.json` — still a syntactically valid gitignore glob, matching no real
filename — and `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3554 passed). Closed by
adding `.chela_selfcheck_cmx337_round3.json` — the exact filename that motivated the pattern,
and matched by no other pattern in the file — to `REAL_SELF_CHECK_SCRATCH_FILENAMES`; under the
mutation `test_gitignore_matches_a_real_self_check_scratch_filename[.chela_selfcheck_cmx337_round3.json]`
fails (`git check-ignore` exit 1) instead of passing.

**See also:** [[26|shape 26]] — the same underlying failure (a claim with zero guard of its own,
hiding behind an otherwise-large green suite) but shape 26 is about a *load-bearing runtime
literal* a PR's prose claims to have changed; here it is one line in a hand-maintained,
already enumeration-tested list that simply wasn't added to its own parallel enumeration.
[[28|shape 28]] — also a partial-close shape, but 28 is about under-closing a note that named
a wider prescription than the blocking finding required; here nothing named the gap at all
until this round, because the pattern's own probe was never written in the first place.
