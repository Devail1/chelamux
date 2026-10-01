## 418b. A host-probing helper is monkeypatched to a constant in every test, so its parser never runs

**Assertion form:** a helper shells out to the host (`ip -o addr show`) and parses the
output into a security-relevant list (addresses the egress proxy must DENY). Every caller
test monkeypatches the helper itself (`host_deny_nets = lambda: ["198.51.100.7"]`) to keep
the test hermetic, then asserts the constant reached the launch argv. The wiring is covered;
the parse is not — no test ever feeds the real regex a sample of the command's output.

**Mutation that defeats it:** `out.add(addr)` → `out.discard(addr)` (or a regex that misses
`inet6`). The helper now returns `[]`, the host's PUBLIC address is no longer denied, and
every test still passes because none of them call the real helper.

**Guard form that survives:** stub one level lower — the subprocess call — with a recorded
sample of the tool's real output (loopback, a public v4, a public v6, docker0), assert the
exact parsed set, and assert it reaches the argv through the real helper. Add the
tool-missing case (`FileNotFoundError` → `[]`) so the best-effort path is pinned too.
