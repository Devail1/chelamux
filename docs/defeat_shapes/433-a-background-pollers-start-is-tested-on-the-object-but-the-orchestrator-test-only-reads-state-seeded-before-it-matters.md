## 433. A background poller's start() is tested on the object, but the orchestrator's test only reads state seeded before the poller matters

**Assertion form:** a long-running entry point (`share_sandbox.run`) does a synchronous first
pass (`mirror.sync()`) and then starts a background thread (`mirror.start()`) that keeps the
state current for the rest of the session. The thread is proven on the object itself: a test
calls `TokenMirror.start(interval=0.01)`, changes the source, and waits for the copy. The
entry point is proven by a separate test that stubs the session's body and asserts the state
the body saw. But that body only reads what the synchronous first pass already wrote, and
nothing changes during the session.

**Mutation that defeats it:** replace `mirror.start()` in `run()` with `pass`. The object-level
test still starts its own thread, and the `run()` test still sees the first pass's value.
The suite stays green. Live, the first refresh of the host's login (hours in) is never
copied, and the session dies on a revoked token. That refresh is the bug the PR exists to fix.

**Guard form that survives:** drive the change INSIDE the entry point's own lifetime. Make the
stubbed session body (here `subprocess.call`, the guest's whole lifetime) change the source,
then wait a bounded time for the state to follow, and assert the new value. Shorten the
interval through the module constant the entry point reads (`TOKEN_POLL_SECONDS`), not by
passing an argument the entry point never passes. That means the default must be resolved at
call time, not frozen into a `def f(interval=CONST)` signature.

**Related:** [[50|entry 50]] (each half proven, the joint between them never run) and
[[352c|entry 352c]] (a module default only exercised through an override).
