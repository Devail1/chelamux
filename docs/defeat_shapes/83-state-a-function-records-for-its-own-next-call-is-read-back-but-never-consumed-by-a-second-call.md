## 83. State a function records for its OWN next call is read back, but never consumed by a second call

**Assertion form:** `agent_manager.follow_folder_names` renames a still-auto window when its
session moves folder, and records the folder it derived the new name from in a tmux option
(`@chela_name_cwd`). The NEXT call reads that option first — `derived_from = name_cwd or
start_path` — so a window that has already followed once is judged against its new folder, not
its spawn folder. The tests asserted the rename (`op-3 -> cute-animals`), asserted the option
was WRITTEN (`fake.windows["@183"]["name_cwd"] == CUTE`), and ran a third tick to show steady
state (`== []`). Every one of those is true whichever link of the `or` is read first: the
option is written regardless, and on the steady-state tick `cute-animals` is not auto for the
spawn folder (`~` → `op`) either, so the window is skipped under both orders.

**Mutation that defeats it:** swap the chain so the spawn folder wins:

```diff
-         derived_from = name_cwd or start_path
+         derived_from = start_path or name_cwd
```

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (6657 passed). In production the window
follows ONCE and is then frozen forever — its new name no longer looks auto for its spawn
folder, so it reads as hand-picked.

**Guard form that survives:** when a function persists state whose only consumer is its own
next invocation, drive that invocation with input where the recorded state and the fallback
DISAGREE on the outcome — here a second move (`cute-animals` → `dogs`), which renames only if
the recorded folder is the one consulted. "The value was written" proves the write, not that
anything reads it; "the next call is a no-op" is usually true under both readings.

**Found:** CMX-83, PR #645, rework round 1. Closed by
`test_a_second_move_follows_from_the_recorded_folder_not_the_spawn_folder`. The same verdict
also found the daemon call-site unwired with the suite green (`followed = []`) — the familiar
"helper tested only by direct calls" shape ([[329|shape 329]], [[44|shape 44]]); closed by
`test_the_daemon_loop_follows_folders_across_two_real_ticks`, which runs TWO real `cmd_run`
passes with the real helper, so it also catches a debounce dict rebuilt per tick (the state the
loop must carry between passes is this same shape, one level up).

**See also:** [[336|shape 336]] — a fallback chain proven only by facts true under every link.
This is that shape where the preferred link is populated only by the function's own earlier
call, so no single-call fixture can ever make the links diverge.
