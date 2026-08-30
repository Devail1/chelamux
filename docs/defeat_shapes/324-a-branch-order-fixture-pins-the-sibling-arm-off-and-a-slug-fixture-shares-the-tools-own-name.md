## 324. A branch-order fixture pins the sibling arm's signal off instead of on, and a slug/name assertion's fixture value collides with text the message already contains for unrelated reasons

**Assertion form:** two related gaps found in the same round. (1) Two mutually-exclusive
reports share one `if condition_a: ... elif condition_b: ...` — condition_a (a load
failure) is meant to pre-empt condition_b (a staleness warning) when both are true, so an
operator is never told to fix the less severe problem first. The guard proving
condition_b's message is absent when condition_a fires sets condition_b's own underlying
signal to its OFF value (`installed_hooks_stale` monkeypatched to `False`) rather than its
ON value — with condition_b already false on its own terms, `elif condition_b:` and a
standalone `if condition_b:` produce the identical (absent) output, so the fixture cannot
tell "skipped because pre-empted" from "skipped because never true." (2) A rendered
message interpolates a field (a vanished marketplace's slug) that a guard wants to prove
is actually rendered, not blanked. The fixture's chosen value is `"chela"` — the same
string as the tool's own name, which the *surrounding* boilerplate of that same message
already contains for reasons that have nothing to do with the field (`` `chela update` ``,
"chela does not know where it came from", the plugins-dir path). The assertion is a bare
substring check, `"chela" in body`/`"chela" in out`, which the boilerplate alone already
satisfies, so blanking the interpolated field changes nothing the assertion can see.

**Mutation that defeats it:**
1. `elif doctor.installed_hooks_stale():` → `if doctor.installed_hooks_stale():`. With the
   fixture's `installed_hooks_stale` pinned `False`, both the real `elif` and the mutated
   standalone `if` skip the stale-hooks message identically.
2. Blank every render of the interpolated slug (`copy.marketplace` → `''`, and separately
   `', '.join(missing_marketplaces)` → `', '.join([])`). The message still contains the
   literal word "chela" from unrelated boilerplate in both cases, so `"chela" in body` /
   `"chela" in out` still passes.
3. A quieter version of mutation 2: switching the fixture's marketplace name to something
   that does not collide with the tool's name (e.g. `"acme"`) is not sufficient by itself
   if that same value is *also* baked into an incidental, non-rendered detail the test has
   no reason to care about — here, `_install()`'s cache directory is literally
   `.../cache/<marketplace>/chela/<version>/...`, so `<marketplace>` shows up in
   `copy.manifest`'s path string too, which the same doctor finding interpolates
   elsewhere. A bare `"acme" in body` check still passes under mutation 2 for the same
   structural reason the `"chela"` one did — through a source that isn't the thing being
   tested.

**Guard form that survives:**
- Branch order: pin the SIBLING branch's own underlying condition to the value that WOULD
  fire it (`installed_hooks_stale` → `True`) while the branch under test also fires, and
  assert the sibling's message is absent. This is the only fixture shape that
  distinguishes "pre-empted" from "never true," the same principle as
  [[308|shape 308]]: prove a gate closes a *live* signal, not an absent one.
- Slug/name assertions: pick a fixture value that does not share text with the tool's own
  name or any other literal the message contains for unrelated reasons, AND assert a
  phrase that can only be produced by the interpolation itself (quoted or
  template-shaped, e.g. `f"chela@{slug}"` → `"chela@acme"`), not a bare substring that an
  incidental path segment or other field could also satisfy.

**Found:** CMX-321 rework round 1, PR #409. Filed as 322 (321 was already claimed on
`origin/dev` by an unrelated, earlier shape, `321-an-idempotent-backfill-migration-...md`,
from a different PR also filed under CMX-321) — but by the time this PR's CI ran, an
independent PR (CMX-322, #408) had *also* landed on `origin/dev` and claimed 322 for an
unrelated shape (`322-a-formatted-log-messages-interpolated-argument-...md`). Two branches
picking the same "next free" number off diverged snapshots of `dev` is exactly the race the
picked-at-file-time number can't see — CI (which checks out the PR merged into `dev`'s
current head) is what actually catches it, not a local run against either branch alone.
Bumped to 324 (323 was already taken by this same PR's other new shape) per the same
backstop in `docs/DEFEAT_SHAPES.md`'s "How this catalog grows": bump to the next free
number rather than collide, re-checking `origin/dev` at push time rather than trusting the
number picked when the branch was created. The judge's
required-mutation-set verdict
named all three mutations above against `chela/main.py` (`cmd_update`'s `elif`) and
`chela/runtime_truth.py` (`_installed_report`'s gone-marketplace `Finding`). Closed by
pinning `installed_hooks_stale` to `True` (not `False`) in
`test_update_names_a_gone_marketplace_distinctly_from_stale_hooks`, and by switching both
that test and `test_doctor_ERRORs_when_the_marketplace_is_gone` from the fixture
marketplace `"chela"` to `"acme"`. The `"acme"`-in-cache-path leak (mutation 3) was caught
by re-applying the blank-slug mutation by hand and confirming the doctor test still
stayed green — closed with the tighter `"marketplace 'acme' is GONE"` / `"chela@acme"`
phrase assertions instead of a bare substring.
