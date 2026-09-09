## 349. A truncation guard's own test checks length bounds, not that any content survived

**Assertion form:** a "truncate a long value instead of dropping it" guard is proven with a
single long fixture and an assertion of the shape `0 < len(result) < len(original)` — "some
output came back, and it's shorter than the input." That reads as proving truncation kept
*something*, but a magnitude check like this is satisfied by any non-empty, shorter-than-input
string, including one made entirely of the function's own decoration (an ellipsis, a fixed
prefix) with none of the actual content in it.

**Mutation that defeats it:** shrink the slice to nothing before appending the truncation
marker — `detail[:_MAX_DETAIL].rstrip() + "…"` → `detail[:0].rstrip() + "…"`. The function now
always throws away the entire detail and returns just `"…"` (plus whatever fixed title/prefix
wraps it), no matter how long the input was. `len(result)` is still `> 0` (the marker isn't
empty) and still `< len(original)` (one character is shorter than five thousand), so the
bound-only assertion is satisfied exactly as well as it was by a correct 500-character
truncation — the test cannot tell "kept the first 500 characters" from "kept nothing at all."

**Guard form that survives:** assert that a specific, sizeable chunk of the *original content*
is actually present in the result — e.g. that the expected number of repeated characters from
the input (`"x" * _MAX_DETAIL`) appears in the output — not just that the output's length falls
somewhere in an open interval. A length-only bound proves a truncation function ran; it does
not prove the function kept anything worth keeping, which is the entire thing the guard exists
to protect (the docstring on this exact guard says so: "truncating to nothing defeats the
point just as badly as never emitting it at all").

**Found:** `chela/doctor.py`'s `_detail_for_notify` (CMX-349 rework round 1, PR #460). The
judge shrank the slice as above in a throwaway checkout;
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3767 passed) with the corruption in
place because `test_a_very_long_detail_is_truncated_not_dropped` asserted only `0 < len(message)
< len(long_detail)`, and `"✗ it broke\n…"` satisfies that bound as well as a genuine 500-char
prefix does. Closed by asserting `"x" * doctor._MAX_DETAIL in message` alongside the existing
bounds, which the emptied-slice mutation fails immediately (no `x` survives at all).
