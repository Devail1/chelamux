---
name: interview-me
description: Elicit requirements by interviewing the user ONE question at a time, highest-impact decisions first, then produce a paste-ready decision record. Use when a request is under-specified and the user can answer questions but hasn't written a spec, or when the user asks to be interviewed / to nail down requirements before work starts. Domain-agnostic (code, research, ops, writing, design). Derived from Thariq's "A Field Guide to Fable: Finding Your Unknowns".
---

# Interview me — one question at a time, impact-first

When a request is under-specified, don't guess and don't dump a wall of questions. Interview the user **one question at a time**, ordering by architectural/decision impact — the answers that most change what gets built come first. The output is a clean **decision record** the user can paste into a prompt, a PR, or `CLAUDE.md`.

## Rules of the interview

1. **One question per turn.** Wait for the answer before asking the next. A single focused question gets a real answer; a batch of ten gets skimmed or ignored.
2. **Highest-impact first.** Ask the question whose answer most changes the shape of the work — data model, scope boundary, success criterion, target/audience — before anything cosmetic. Re-prioritize after each answer: a reply often makes later questions moot or spawns a more important one.
3. **Offer a default in every question.** Frame as "I'll assume X unless you'd rather Y" so the user can approve-by-picking, not author from scratch. Make the recommended default explicit.
4. **Only ask what you can't determine yourself.** If you can read it from the code, the files, or prior art, do that instead of spending a question. Interview for *intent and preference*, not for facts you can verify.
5. **Know when to stop.** Stop once the remaining unknowns are low-impact enough to just pick sensible defaults. Don't interrogate past the point of diminishing returns — say "I have enough to proceed; I'll default the rest" and move on.

Use the interactive question tool for genuine forks with no sensible default; use plain prose for a quick "assuming X, ok?" check.

## The decision record (the deliverable)

When the interview ends, emit a compact, paste-ready record:

```
## Decisions
- <topic>: <chosen answer> (was: <the fork / alternatives considered>)
- ...

## Defaults applied (unasked or low-impact)
- <topic>: <default taken> — change if wrong

## Still open / deferred
- <topic>: <why deferred, what would resolve it>
```

Keep it terse and scannable. This record is the *map* you'll build against — and a durable artifact the user can drop into `CLAUDE.md` or a PR so the next session inherits the resolved intent instead of re-deriving it.

## Pairs with
- **blindspot-pass** — surfaces which unknowns exist by exploring the territory; interview-me *resolves* the human-judgment ones by asking. Blindspot first to find the forks, interview to close them.
- **implementation-plan** — feed the decision record straight into an impact-ordered plan.
