---
name: implementation-plan
description: Produce an implementation plan ordered by LIKELIHOOD-OF-CHANGE, not chronology — load-bearing/uncertain decisions (data models, interfaces, contracts, user-facing choices) lead; mechanical low-ambiguity work sinks to the bottom. Use when planning any non-trivial change before writing code, or when the user asks for an implementation plan. Domain-agnostic. Derived from Thariq's "A Field Guide to Fable: Finding Your Unknowns".
---

# Implementation plan — order by likelihood-of-change

Most plans are written in *execution order* ("scaffold, then data layer, then UI, then wire it up"). That's the wrong sort key: it buries the decisions most likely to be wrong — and most expensive to unwind — in the middle, where they get assumed instead of scrutinized.

**Sort the plan so the decisions most likely to change come first**, and mechanical low-ambiguity work sinks to the bottom. Front-load the conversation onto the forks that actually matter, so the load-bearing unknowns get locked down *before* anything is built on top of them.

## How to order

Rank each step by two axes and let their combination set position:

- **Likelihood of change** — how uncertain / contested is this decision?
- **Blast radius** — how much else has to be rewritten if it changes?

High on either → **top of the plan**. Low on both → **bottom**.

**Top (decide first — uncertain and/or high blast radius):**
- Data models, schemas, storage shape
- Type/interface definitions, function signatures, module boundaries
- API contracts and integration points between components
- User-facing choices (behavior, UX, defaults) — cheap to change on paper, expensive after they're wired in
- Anything with no obvious right answer, or where you'd otherwise silently guess

**Bottom (bury it — mechanical, one obvious right answer):**
- Renames, file moves, import reshuffles
- Boilerplate, wiring, scaffolding
- Formatting, comment/docstring passes
- Straight refactors with no behavioral decision

## Output shape

Produce a numbered plan where **step 1 is the riskiest decision, not the first thing you'd type.** For each of the top (load-bearing) steps, include:

- **The decision** and the **default** you'd take unless told otherwise
- **What depends on it** (the blast radius — why it's high in the list)
- **The open question**, if it's a genuine fork with no sensible default (surface these early so they're resolved before code, not after)

Then the mechanical steps as a terse checklist at the bottom — no decisions to belabor there.

Keep the load-bearing section rich and the mechanical section brief. If the plan has *no* high-uncertainty decisions, say so plainly and just list the steps — don't manufacture drama.

## Pairs with
- **blindspot-pass** — that skill *finds* the unknowns by exploring the territory; this one *sequences* the work so the riskiest of them are settled first. Run the blindspot pass, then order the plan by what it surfaced.
