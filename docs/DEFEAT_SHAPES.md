# Defeat shapes — a catalog of guards that look like they work and don't

⚖️ [The judge](../chela/judge.py) exists because a passing suite is not proof: a guard can
be written in a shape where the invariant it claims to protect can be **corrupted** —
deleted, inverted, dead-coded, unwired — and the suite stays green anyway. Every shape in
this catalog was found *live*, by a judge round or a human review, on this repo. Before this
catalog existed that knowledge lived only in the comment above the one test that closed it —
reachable to someone already reading that file, and nobody else. The person writing the next
dispatch brief, or the agent designing the next guard, walked into the same trap a second
time because the first fix never left its test file.

**This is a catalog of measured defeats, not a checklist someone imagined.** Every entry
names the PR or test that found it.

## Where the entries live

Each shape is its own file under [`docs/defeat_shapes/`](defeat_shapes/) — `ls` it, or open
it in your editor, to browse the whole catalog. **This file (`DEFEAT_SHAPES.md`) is a static
pointer, not an index you maintain by hand** — that split is deliberate (see below), so
reading it just means opening the directory; there is no separate list here to fall out of
sync with what's actually in it.

## How this catalog grows

- **Writing a guard?** Check the shapes under `docs/defeat_shapes/` against what you're about
  to write *before* you write it — most of these look completely reasonable until you ask
  "what corruption would this miss?"
- **Reworking a `SURVIVED` verdict?** The judge names the guard and the mutation that defeated
  it (see `chela judge`'s block comment). If that shape isn't catalogued yet, add it as part
  of the same fix: create **one new file** in `docs/defeat_shapes/`, numbered after **your own
  CMX task number** (`NNN-slug.md`, e.g. task `CMX-301` → `301-your-shape-slug.md`) — **not**
  "one past the current highest". "Current highest" means reading `dev`'s file listing off
  whichever checkout your branch forked from and guessing; every concurrent agent computing
  that guess independently is *by construction* the collision, because each one's "highest"
  is already stale the moment a sibling branch is also picking a number. Measured 2026-08-16:
  CMX-298 merged to `dev` taking shapes 62–68 while two sibling branches were already in
  flight — cmx-299 had independently picked `62,63,64,65,66,67` (a six-way collision) and
  cmx-300 had picked `62` — and a human had to hand-allocate disjoint ranges from outside
  either branch to unstick them. Your CMX task number doesn't have this problem: the
  dispatcher hands it out from a single, centrally serialized counter, so two branches in
  flight at once never receive the same one — reuse that number instead of computing a new
  one from a listing. (Numbers only need to stay unique, not contiguous — see below — so
  gaps between task-numbered entries and the legacy sequential range below them are expected
  and fine.) The judge itself never commits to this repo (its checkout is a throwaway
  detached copy, deleted when it finishes), so the agent doing the rework is the one with a
  branch to put the new entry on.
  - **Why a new file, not a new section appended to one shared file:** the catalog used to be
    a single file, and every concurrent rework appended its new entry to the same tail —
    guessing the next number from whatever HEAD it happened to branch from. Two reworks in
    flight at once always produced the same git conflict on the same lines, needing a hand
    renumber every time (measured: four times in 24h on 2026-08-14). A new file has no shared
    lines to collide on — two reworks adding `21-foo.md` and `21-bar.md` concurrently merge
    cleanly even if they picked the same number.
  - **The number still has to be unique, though.** An earlier version of this doc called the
    number "a readability aid, not an enforced key" — that was wrong: this catalog's own
    cross-references ("shape 37", `[[21|entry 21]]`) and every "DEFEAT_SHAPES #N" citation
    scattered across the test suite point at a *number*, not a filename, so two files
    claiming the same one make every such reference ambiguous (measured: shape 37 landed
    twice on `dev` with no signal, CMX-293). A test asserts the numbers are unique across
    `docs/defeat_shapes/`.
  - **Need a second (or third) shape on the same branch?** ⛔ Do **not** bump to a different
    number — "any other free one" used to be the instruction here, and it was the one legal
    way a decentralized guess could still enter: `cmx-321`'s second shape found `321` already
    taken, "bumped to any other free one" per this doc's own old wording, and landed on `323`
    — a number that belonged to `cmx-323`, a different branch in flight at the same time. `dev`
    only went red once both merged; each PR alone looked clean. Suffix a lowercase letter onto
    your OWN number instead: `328-first-slug.md`, then `328b-second-slug.md`, then
    `328c-third-slug.md`. The `328` namespace belongs exclusively to `cmx-328` — a sibling
    branch can never collide with a suffixed file in it, because it has no reason to ever touch
    `328*` at all — so this needs no listing read and no coordination with anyone, the same
    property the plain task-number rule already has. A test enforces this too: every file this
    branch adds must be numbered `{your task number}` or `{your task number}` + a single
    lowercase letter — no exception, for any reason.
- Each entry: the **assertion form** (how the guard was written), the **mutation that
  defeats it** (what corruption slips through), and the **guard form that survives** (how to
  write it so the same corruption goes red).
