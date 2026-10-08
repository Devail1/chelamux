## 29b. A reader fix threaded through every caller is proven end-to-end through only one, and the other callers' tests stub the reader itself

**Assertion form:** a shared reader (`context.live_snapshot(name)`) gains a disambiguating
argument (`window_id`, CMX-29) so it serves the window's OWN session instead of whichever
session wrote the shared cache file last. The fix threads the new argument through **every**
caller (`/api/agents/context` and `/api/cost?window=live`). The new collision test (two
sessions writing one window's file, in both orders) drives the real hook and the real reader,
but only through the first caller's route. The second caller's existing tests patch
`live_snapshot` with a stub that returns a canned dict per name.

**Mutation that defeats it:** drop the new argument at the second caller
(`context.live_snapshot(name, wid)` → `context.live_snapshot(name)`). The collision test never
hits that route, and the stubbed tests can't tell which arguments the stub got, since the stub
answers by name either way. The Cost tab goes back to showing the background agent's $48 on
the interactive window's row, and the suite stays green.

The prune wiring in the same PR (`prune_snapshots` → `_prune_session_cache`) is the same
pattern one level down. That is shape 338: the helper was proven by calling it directly, and
nothing checked it from the public entry point the daemon actually calls.

**Guard form that survives:** for each caller the fix touched, run the same collision fixture
through that caller's own output (here, `/api/cost?window=live` rows, parametrized over both
write orders). An argument dropped at any one caller then serves the last writer in one of the
orders and goes red. Prove a cleanup helper's wiring through the public function that calls
it (`prune_snapshots(30)` deletes the stale file), not the helper. When grepping for callers
of the reader, count every hit that has the new argument. Each one needs a test that reads its
output.
