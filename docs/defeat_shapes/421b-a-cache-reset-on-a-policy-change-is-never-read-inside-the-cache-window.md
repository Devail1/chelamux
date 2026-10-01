## 421b. A cache reset on a policy change is never read inside the cache window

**Assertion form:** a gate caches its verdict for a short TTL (`Bridge._sandbox_ok` re-checks
the sandbox at most every `SANDBOX_RECHECK_INTERVAL`), and a policy change clears that cache so
the new policy is judged fresh — `self._sandbox_checked_at = None` in `Bridge.set_mode`
(CMX-421). The tests of the change either start from an empty cache (a view-only share never
consults the sandbox, so nothing was cached) or let the fake clock run past the TTL before the
next frame. Either way the next check is fresh with or without the reset.

**Mutation that defeats it:** delete the reset. Every test still gets a fresh verdict, from the
empty cache or the expired TTL, so the suite stays green. Live, a share that verified a moment
ago keeps typing for up to one TTL after the window stops being a sandbox. In the other
direction, a refusal cached just before the upgrade blocks a now-verified guest.

**Guard form that survives:** fill the cache under the OLD policy first: type once so a verdict
is stored. Then flip the underlying truth, change the policy, and send the next frame with the
clock still INSIDE the TTL. Assert the outcome matches the new truth. Drive both directions
(cached OK → now refused, cached refusal → now allowed), because an implementation can get one
right by accident.

**Related:** [[421|entry 421]] (one exit edge of a state tested). Entry 325 is a nearby but
different shape: an untested path that falls back to the unsafe default.
