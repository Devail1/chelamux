## 418. A breakage table for a multi-conjunct shape check flips only SOME of its conjuncts

**Assertion form:** a `verify_*(inspect) -> str | None` function checks a container's live
shape against the shape the launcher created — one long `or`-chain of refusals
(`Privileged or CapAdd or "ALL" not in CapDrop or not ReadonlyRootfs or …`) plus a mount
loop whose accept arm is itself a conjunction (`dst == SCRIPT and not rw and src == ours`).
The test is a parametrized table of "flips", each a single breakage of a known-good fixture,
with a negative control proving the good fixture verifies. It *looks* exhaustive because it
has a dozen rows — but the rows were written from the threats the author thought of first
(privileged, docker.sock, other cmd, writable root), not by walking the predicate.

**Mutation that defeats it:** delete any conjunct no row targets — `"ALL" not in CapDrop`
→ `False`, or drop `not rw` from the mount's accept arm. Every row still fails for its own
reason, the good fixture still verifies, the suite stays green, and a sidecar that kept
its capabilities or mounted its own code writable now verifies.

**Guard form that survives:** derive the table FROM the predicate: one row per conjunct of
every refusal chain and per conjunct of every accept arm (here: CapDrop missing, CapDrop
partial, CapAdd set, no-new-privileges missing, other user, other label; script mount RW,
script mount from another source, log mount pointed elsewhere). When a conjunct is added
to the check, its row is added in the same diff.

**Distinct from [[04|shape 04]]** (a compound mutation proving the pair, not either half):
there each half was mutated together; here the uncovered conjunct is simply never flipped.
