## 149. A workflow `run:` block is substring-matched, so its loop exit can be dead-coded

**Assertion form:** a CI step's logic lives in a multi-line `run:` shell block — here
`deploy-landing.yml`'s "Verify the production URL serves this commit", a 10-attempt
`for` loop that fetches the page, `if <compare>; then echo ✅; exit 0; fi`, sleeps, and
`exit 1`s after the last attempt. The guard loads the YAML and asserts on the block as
text: the `if python3 scripts/landing_verify.py …; then` line is present, `curl` comes
before it, the old `diff -q` is gone, the block ends in `exit 1`. The comparison script
itself is tested thoroughly through its CLI. It reads as "the step gates on the script".

**Mutation that defeats it:** the success branch's `exit 0` → `true`. A match no longer
ends the loop: the step keeps fetching, sleeps through all ten attempts, and falls through
to `exit 1` — every deploy that WORKED now goes red with "never matched". Every asserted
substring is still there. It is [[99|shape 99]] in shell: a text guard sees the line it
names, never which way control leaves it.

**Why it looks covered:** the interesting logic (what counts as a match) was moved into a
script and tested there, so the few lines of shell left behind feel like wiring — and
wiring got a structural check. But those few lines decide what the step's exit code IS.

**Guard form that survives:** execute the workflow's own `run:` text under `bash`, in a
temp tree holding the real script and a fixture `landing/index.html`, with the network and
the wait stubbed by fake `curl`/`sleep` executables prepended to `PATH` (each appends to a
call log; `curl` serves attempt *n*'s fixture to its `-o` path). Assert exit code AND call
counts: a match on attempt 1 → exit 0 with 1 fetch and 0 sleeps; production catching up on
attempt 3 → exit 0 with 3 fetches; a never-matching page → exit 1 after 10. The call count
is what pins "a match ENDS the loop" rather than merely "the step went green eventually".

**Found:** CMX-149 judge round 1, PR #692. Fixed in `tests/test_landing_verify.py`
(`_run_verify_step` and the three `test_verify_step_exits_*` tests).
