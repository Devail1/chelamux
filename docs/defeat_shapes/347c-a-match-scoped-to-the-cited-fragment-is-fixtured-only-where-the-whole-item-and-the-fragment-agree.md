## 347c. A match scoped to the cited fragment is fixtured only where the whole item and the fragment agree

**Assertion form:** a relevance check reads a narrow input on purpose. `_about(issue, title,
text, quote, key)` asks whether the **quote** (the fragment the model cited) holds a word of
the issue's title, while the id check reads the whole cited **item**. Every fixture's item
carries its title word inside the quoted fragment (`"wire the parser"` quoted from
`"CMX-5: wire the parser"`), and every title word is long (`parser`, `export`, `docs`). The
tests assert "kept" or "evidence_unrelated" and are green.

**Why it doesn't guard:** the fragment and the item agree on every fixture, so the check
cannot tell which one it read. Widening it to the whole item, which lets a quote of
unrelated text ride on a title word elsewhere in the item, gives the same result. The
keyword floor (`KEYWORD_MIN_CHARS = 4`, meant to keep "the" or "fix" from matching
everything) has the same gap: no title in the fixtures has a short word, so dropping the
floor to 1 changes nothing that a test can see. In the flag_stale branch, every item that
names an In Progress issue by id also holds a word of its title, so the id clause can be
deleted without a failure ([[62b|shape 62b]]).

**Mutations that defeated it:** (CMX-347)

```diff
- if not _about(issue, title, text, quote, key)), None)
+ if not _about(issue, title, text, _norm(text), key)), None)
- KEYWORD_MIN_CHARS = 4
+ KEYWORD_MIN_CHARS = 1
```

**Guard form that survives:** give each input a fixture where the two scopes disagree: an
item whose title word sits only OUTSIDE the quote (dropped), and the same item quoted where
the word is inside it (kept). Drive a threshold from both sides: a title made only of
words shorter than the floor (`"a fix for the cli"`, with "the" in the quote) must not
match, and a word exactly at the floor (`"wire"`) must. For an `or` of id and word, use one
fixture per clause, with the other clause false, in each kind of item.

**Related:** [[342|shape 342]] (two quantities that coincide in every fixture), [[62b|shape 62b]],
[[26b|shape 26b]] (a threshold fixtured only on one side of it).
