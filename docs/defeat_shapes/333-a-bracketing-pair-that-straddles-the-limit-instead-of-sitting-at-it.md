## 333. A bracketing pair that straddles the limit instead of sitting at it

**Assertion form:** a guard proves a truncation boundary with two fixtures, one below the
limit and one above it — "N-1 chars survive whole, N+1 chars get cut" — intended to bracket
the exact cutoff `N`. But neither fixture's length is `N` itself; both sit one character off
on either side. The pair proves the effective limit is *somewhere in* `{N-1, N}`, not that it
is `N` — the one length that would actually distinguish `<= limit` from `< limit` is never
exercised.

**Mutation that defeats it:** flip the comparison from `<= limit` to `< limit` (or the
inverse). The effective cutoff silently shrinks (or grows) by one, but both bracketing
fixtures stay green: the below-limit fixture is now further from the boundary than it thinks
(`N-1 < N-1` is still true), and the above-limit fixture was already past the new boundary too.
Nothing in the pair ever asks what happens to a value of length exactly `N`.

**Why this looks like it already closes the boundary:** "one under, one over" reads as a
complete bracket, and for many mutations it is — an off-by-a-lot bug, a limit that moves to a
different number entirely, or a `>` swapped for `<` all get caught. It only misses the single
narrowest mutation: `<=` vs `<` (or `>=` vs `>`), which moves the boundary by exactly the one
unit the fixtures declined to spend.

**Guard form that survives:** when bracketing a boundary, spend the exact value once — a
fixture of length exactly `N` that must land on the "kept whole" side (if the guard is meant
to be inclusive) is strictly tighter than a `N-1`/`N+1` pair and costs nothing extra (widening
an existing under-limit fixture by one or two characters, in this case). The `N+1`
above-the-limit fixture is still worth keeping since it pins that truncation fires at all.

**Found:** CMX-333 rework round 1 (2026-09-01), PR #425. `_short_title`'s boundary is
`if len(text) <= limit`. The round-1 fixtures were a 219-char sign-off ("kept whole") and a
221-char sign-off ("truncated"), meant to bracket `FINAL_MESSAGE_CHARS` (220). The judge's
mutation flipped the comparison to `< limit`, shrinking the effective cap to 219 — both
fixtures stayed green, because 219 is still `< 219` false... `<= 219` true either way, and 221
was already past both the old and new boundary. Closed by widening the 219-char fixture to
exactly 220 chars (`test_a_sign_off_at_exactly_220_chars_is_kept_whole`), which fails under
the `< limit` mutation and passes under the original `<= limit`.
