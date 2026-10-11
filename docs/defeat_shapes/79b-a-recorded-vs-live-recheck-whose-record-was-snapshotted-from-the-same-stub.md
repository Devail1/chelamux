## 79b. A recorded-vs-live re-check whose record was snapshotted from the same stub

**Assertion form:** a two-phase flow records a value at phase 1 (an override request stores
the PR's `base`) and re-checks it against a live read at phase 2 (the approval refuses to
merge if `gate["base"] != rec["base"]`). Every test drives both phases through the SAME
GitHub stub (`_github(base="dev")`), so the record is a snapshot of the very value it is
later compared against. The suite has tests for "the live base is forbidden at approval"
(`main`), but an earlier, stricter clause in the gate refuses those first — so the
recorded-vs-live clause is never the one that decides anything.

**Mutation that defeats it:** dead-code the re-check
(`if gate["base"] != rec.get("base"):` → `if False and …`). With record == live in every
fixture, and every *forbidden* live base already refused upstream, no test ever reaches a
state where only this clause stands between the approval and the merge — 6606 green.

**Guard form that survives:** make the record and the live read DISAGREE while the live read
alone is still acceptable to every other clause: write the record's `base` to something else
(`overrides.set_fields(rid, base="release")`) and approve against a live `dev`. Then assert
no merge, the request `failed` with the base-changed reason, and the operator told. The test
for "is this re-check reachable at all" is: can you name a fixture where every *other* clause
passes and only this one fails? If not, it is not tested.

**Found:** CMX-79 rework round 1 (2026-10-11), PR #642 — `chela/contract.py::complete_override`.
Same round also surfaced two catalogued shapes: [[407|shape 407]] (the service-channel env
exemption was never driven with `CLAUDECODE`/`$CHELA_ACTOR` present, because the suite's
autouse fixture strips them) and [[32|shape 32]] (the Telegram card's "judge ran on another
commit" branch was asserted only via the judge state, which both branches print).
