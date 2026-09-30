## 405. A region extractor is tested only on its leading side, so its terminator is unguarded

**Assertion form:** a function reads one bounded region out of a larger text, such as "the
BOUNDARIES paragraph of a brief, up to the next heading". It then acts on what it found. The
guard puts the trigger word *before* the region's start marker (in OBJECTIVE) and asserts
that nothing is inferred. That proves the extractor does not start too early. It says nothing
about where the region *ends*, because the fixture has nothing after the region that could
leak in.

**Mutation that defeats it:** remove one heading from the terminator's alternation. For
example, `(?=\*\*(?:WHY|OBJECTIVE|GUARDS|VERIFY|NOTES?)\b` becomes
`(?=\*\*(?:WHY|OBJECTIVE|VERIFY|NOTES?)\b`. The region now runs past `**GUARDS**` to the end
of the text, so a path named only in GUARDS is read as a boundary. The guard stays green:
its trigger word sits on the one side the mutation never touched. (CMX-405, PR #557.)

**Guard form that survives:** test both edges of the region. For **each** terminator the
extractor honours, in every syntactic form it accepts (here both `**GUARDS.**` and a bare
line-start `GUARDS.`), place the trigger word only *after* that terminator and assert that
nothing is found. Pair it with a positive control: the same fixture with the word moved inside
the region must be found. The control shows the empty result comes from the terminator and
not from the pattern missing the word. Parametrize over the alternation instead of picking
one representative heading, because each alternative can be deleted on its own.

**Why this is distinct from [[12|shape 12]]:** shape 12 is about a stop rule nobody reaches
because every fixture stops earlier. Here the start boundary *is* exercised. The *end*
boundary is the one no fixture reaches, and a region has two edges that fail independently.
