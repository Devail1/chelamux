## 352f. A tooltip-builder's own opening line is the one clause no fixture ever reads back

**Assertion form:** `_taskProgressChip` in `chela/dashboard/static/js/dispatcher.js` builds
its tooltip as an array of lines — `lines = [`${tasks.done} of ${tasks.total} tasks done`]`
— then conditionally appends an in-progress line and, per shape [[352b|352b]]'s fix, one line
per blocked task. Round 1 pinned the in-progress line with
`assert.match(chip.getAttribute('title'), /In progress: .../)`; round 2 pinned both blocked-by
arms the same way. Both assertions use a bare regex against the whole `title` string, so
neither one cares *where* in the title its own line sits — each only proves its own line is
present *somewhere*. The array's first element — the summary line, unconditionally present on
every chip regardless of fixture, since `in_progress` and `blocked` are both optional fields —
was never the subject of any assertion at all. Two siblings got mirrored positive controls;
the parent clause that seeds the array before either sibling runs did not, because "mirror the
sibling" (352b's fix) only reaches clauses that already have a structural twin to copy from —
the opening line has none.

**Mutation that defeats it:** replace the array's seed value with an empty string —
``const lines = [`${tasks.done} of ${tasks.total} tasks done`];`` ->
``const lines = [''];``. `node --check` still parses the file;
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` still reports all tests passed, because the
in-progress and blocked-by assertions match their own substrings anywhere in `title`, and no
assertion in the suite ever inspects the title's first line — or its full literal value —
at all.

**Guard form that survives:** when a tooltip (or any multi-line rendered string) is assembled
by seeding an array with one unconditional clause and then appending N conditional siblings,
the unconditional seed needs its own assertion, not just coverage-by-proximity from whichever
conditional clause happens to render next to it in a given fixture. A regex that only checks
"does this substring appear anywhere" is blind to *position*, so pin the seed specifically —
here, `assert.match(title, /^3 of 4 tasks done/)` — rather than trusting that a assertion on a
later line implies the earlier one was checked too. The general form: every distinct line/field
a render function contributes needs its own read-back, including the one line that has no
conditional sibling to be compared against and therefore never shows up as an obviously
asymmetric gap in a review.

**Found:** CMX-352 rework round 4, PR #465. The judge's required-mutation-set verdict named
this exact mutation verbatim (``const lines = [`${tasks.done} of ${tasks.total} tasks done`];``
-> ``const lines = [''];``), having applied it to a throwaway checkout of round 3's head and
found the suite still green.

**See also:** [[352b|shape 352b]] — the sibling clause (the blocked-by loop) that got the
mirrored fixture-and-assertion treatment this shape's clause did not, because it had a
structural twin (the in-progress line) to be mirrored against and the seed line had none.
[[352|shape 352]] — the original guard-gap this feature shipped with.
