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
  of the same fix: create **one new file** in `docs/defeat_shapes/`, numbered one past the
  current highest (`NN-slug.md`, e.g. `21-your-shape-slug.md`) — the judge itself never
  commits to this repo (its checkout is a throwaway detached copy, deleted when it finishes),
  so the agent doing the rework is the one with a branch to put the new entry on.
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
    `docs/defeat_shapes/`, so a collision fails loudly on your branch — bump your file's
    number (and its heading) to the next free one and move on; it's a local, one-line fix,
    same as resolving any other rebase conflict.
- Each entry: the **assertion form** (how the guard was written), the **mutation that
  defeats it** (what corruption slips through), and the **guard form that survives** (how to
  write it so the same corruption goes red).
