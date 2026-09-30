## 403. An imported verifier's refusal list is proven only by the one refusal the integration test needed

**Assertion form:** a security verifier arrives in the repo "as-is, hand-verified" from
outside it (`share_sandbox.verify_container` / `verify_pane`, CMX-403, taken from the CMX-400
brief). It is a flat run of about ten early-return refusals: privileged, writable rootfs, no
`no-new-privileges`, wrong user, wrong network set, no `inhibit_ipv4`, unexpected mount,
non-docker child process. The PR's own work is the *consumer*, the host typing gate, so its
test is parametrised over "ways the check can fail": shell parent, wrong argv, unreadable
`/proc`, `docker inspect` error, and ONE container breakage (`Network.Internal = False`).
Every case asserts that input is dropped. It reads as "fail closed on every failing check".

**Why the obvious tests don't catch it:** the parametrisation covers the *consumer's* inputs,
meaning where the `False` verdict comes from, not the *verifier's* branches. Nine of the ten
container refusals are never driven by any fixture. Each one can be dead-coded
(`if False and …`, or `return …` → `continue`) and the suite stays green, because the one
breakage the test does drive (`Internal`) still refuses. The hand verification upstream was
real, but it was a one-time measurement. Nothing in the repo keeps it true.

The same PR repeats the shape on its fail-closed `except` in `_sandbox_ok` (the stub never
raises, so the fallback verdict can flip to `True`). It repeats it again on the policy kwargs
of `start_bridge`: every route test monkeypatches `start_bridge` away (shape [[319]]), so
`allow_typing` / `unsandboxed` were shown to *leave* app.py but never to *arm* a Bridge.

**Mutation that defeats it:** dead-code any single refusal, e.g.
`if hc.get("Privileged") or …` → `if False and hc.get("Privileged") or …`. Mind Python
precedence here: the mutation leaves `CapAdd` and `CapDrop` live, so a fixture that sets
Privileged *and* CapAdd still refuses and hides the gap. 12 mutations survived
(4421 passed).

**Guard form that survives:** one parametrised row per refusal. Each row flips exactly ONE
field of a known-good fixture, and every other field stays good, so no sibling refusal can
mask the row under test. Assert the specific reason substring. The untouched fixture
verifying `(True, "")` in the same test is the negative control. For a fail-closed `except`,
make the stub *raise*, and assert the gate drops input. For a stubbed factory's kwargs, add
one test through the REAL factory with only its side effects (threads, sockets) stubbed, and
read the policy back off the object it built.
