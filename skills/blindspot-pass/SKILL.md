---
name: blindspot-pass
description: Surface the unknowns BEFORE doing the work — explore the territory, restate the plan, and report the questions the user didn't know to ask. Use at the start of any non-trivial or unfamiliar task, before writing code / making changes, whenever the request is under-specified, or when the user asks for a "blindspot pass". Domain-agnostic (code, research, ops, writing). Derived from Thariq's "A Field Guide to Fable: Finding Your Unknowns".
---

# Blindspot pass — find the unknowns before you act

**The map is not the territory.** The prompt, the docs, and `CLAUDE.md` are a *map* of the work. The real system — its history, undocumented constraints, and the user's actual intent — is the *territory*. The gap between them is the **unknowns**.

The trap: a capable model resolves ambiguity *confidently* and propagates a wrong assumption across the whole task — a failure caught late and compounding, not early and local. So the highest-leverage move is not a better prompt; it's surfacing the unknowns **before** committing to the work.

Run this pass **before** implementing whenever the task is non-trivial, the area is unfamiliar, the request is under-specified, or the cost of a wrong assumption is high. Skip it for genuinely trivial, well-specified, one-shot tasks — say you're skipping it and why.

## The pass — four moves, in order

### 1. Explore the territory (don't rely on the map)
Actually look before answering. Read the relevant code / files / data / prior art — the *real* thing, not your memory of how such things usually work. Note what's actually there versus what the request assumed is there. Cheap exploration now beats an expensive wrong guess later.

### 2. Restate the plan in your own words
Paraphrase back what you're about to do and why — the goal, the approach, the scope boundaries. A restatement that *surprises the user* is the point: it exposes a missing assumption while it's still cheap to fix. Keep it short; this is a mirror, not a spec.

### 3. Report the unknowns you found
Lead with this — it's the deliverable. Output a tight list under these headings (drop any that are empty):

- **Decisions I'd otherwise guess** — the forks where I'll pick something unless you say otherwise, with my default for each. These are the ones that silently propagate.
- **What "good" looks like** — the acceptance bar / definition of done, as I understand it. Wrong here = wrong everything.
- **Territory surprises** — things in the real system that contradict or complicate the request (a constraint, prior art, a dead end already tried, a dependency).
- **Questions you didn't know to ask** — the ones that only surface once you've looked at the territory.
- **How to prompt me better next time** — what context, if it lived in `CLAUDE.md`/a skill/the request, would have closed this gap up front.

### 4. Resolve, then proceed
Attach a **default** to every open decision so the user can approve-by-silence. Ask only the questions whose answer actually changes what you do (use the question tool for genuine forks with no sensible default). Don't ask what you can verify yourself. Then act.

## Close the gap permanently
When a *first pass* comes out reasonable-but-wrong, that's signal, not just a correction to make: the map has a hole. Offer to write the fix into the durable layer — `CLAUDE.md`, a skill, a memory, a PR "for agents" note — so the same unknown never re-bites. A one-off correction that isn't captured will recur.

## Output shape
Keep it scannable — a few bullets per heading, defaults in **bold**, no preamble. This is a pre-flight check the user reads in ten seconds and either nods at or corrects, not a document. The goal is to turn silent wrong guesses into visible, cheap-to-fix decisions.
