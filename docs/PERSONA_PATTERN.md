# The persona pattern

**Mechanical facts in code, judgment in the LLM — the judge, generalized to the whole persona
layer.**

> Status: v0.1 design. The judge is the *proven* instance of this pattern; the critic and the
> orchestrator are its generalization. Companion to [`ESCALATION_CONTRACT.md`](ESCALATION_CONTRACT.md)
> (the *when*) and [`ORCHESTRATOR_PERSONA.md`](ORCHESTRATOR_PERSONA.md) (the *how*); this doc is the
> *why-and-shape* the other two build against.

---

## The principle (already proven by the judge)

The judge is trustworthy for one reason: **"the reviewing agent decides NOTHING."** The LLM only
*proposes* experiments `(file, before, after)`; the **verdict is a fact computed in code** — a guard
that survives a live, minimal, valid corruption is not a guard. A wrong LLM opinion **cannot** cause
a wrong BLOCK, because blocking requires a mechanically-verified fact.

Generalize that one sentence:

> **Keep the LLM in the generative / judgment role. Put every decision that is *unsafe if wrong*
> into code.**

The reason to trust an autonomous persona is **not** "the model is smart enough to follow the
rules." It is **"the model *cannot break* the rules, because the rules are code."** That is what the
judge demonstrated, and it is the whole architecture.

---

## The split, per persona

### Judge — the reference (built: `chela/judge.py`)
- **LLM proposes:** mutations to the guards the PR claims to add.
- **Code decides:** applies each mutation, proves it landed *and* still parses, runs the suite,
  adjudicates. Green-under-corruption ⇒ **BLOCK** — a fact.
- **The rule:** block on mechanical facts only; `cannot_verify` → a human; opinions → an advisory PR
  comment, never a block.

### Critic — v1 built (`chela/critic.py`, CMX-88): the code half, advisory-only
- **LLM proposes:** design / necessity / scope opinions ("is this the right work," "this reads
  awkward") — **advisory**. *(The judgment half — the next slice; not wired in v1, which keeps
  the lowest-risk slice free of new agent-spawn surface.)*
- **Code decides / gates (facts):** does the brief carry the four mandatory fields
  (objective / boundaries / guardrails / verify)? does a queued task collide with an in-flight task's
  files (the coupling check)? does a PR touch files *outside* its stated scope? These are facts, so
  they can **gate** — reject a malformed brief *before* an agent is spent, flag scope-drift on a PR.
- **v1 (`critic.py`) is the lowest-risk slice: ADVISORY-ONLY and BRIEFS-ONLY.** Both mechanical
  facts ship — the **four-field check** and the **file-coupling check** (the queued brief's target
  files vs. those of in-flight runs) — computed in code the moment a task is picked for dispatch
  and surfaced as a *note* on the run row (`critic_notes`); they gate NOTHING yet. ⚠️ Both review
  the **task-specific brief** (the TODO item the human wrote), NOT the rendered WORKFLOW.md
  template — the template is field-complete boilerplate identical on every dispatch, so reviewing
  it would report "complete" every time and the critic would never fire. It is enforced advisory:
  it runs *after* the agent is launched and every failure is swallowed, so a wrong critic — or a
  crashed one — cannot block, delay, or change a dispatch (the analog of the judge's "decides
  nothing"). The PR trigger (scope-drift) is the judge's slot and is out of v1 scope; gating on the
  facts is a later slice, once the advisory has earned trust.
- **Same rule as the judge:** block on facts, opine on the rest. A wrong *advisory* opinion costs a
  glance, not a rework round — which is exactly why the judgment half is allowed to be fallible.
- **Triggers:** a newly-*queued task* (critique the brief before spending an agent — "plan review is
  the new linter") and a fresh *PR* (structural gate + advisory).

### Orchestrator — to build (the harness), modeled on the judge
- **LLM proposes:** which task next, how to scope a brief, how to read a diff, what to recommend on
  an escalation.
- **Code decides / enforces:** the escalation contract's hard boundaries —
  - **NEVER, enforced in code:** cannot merge to `main`, cannot run a destructive op, cannot execute
    agent-authored text, cannot spend money. The code **refuses**, regardless of what the LLM
    "decides" or what a prompt-injection instructs.
  - **Required-escalation routing, in code:** a security-labelled finding, a judge `cannot_verify`,
    an unreadable gate → **routed to the human**, not decidable by the LLM.
  - **The autonomous gate, in code:** merge only if *CI-green AND judge-clean AND verification-logged
    AND target = `dev`*. `chela merge <run>` checks this; the orchestrator never calls
    `gh pr merge` raw.
- **The LLM operates only inside the mechanically-safe envelope.** Not *"decides nothing"* (the
  orchestrator *must* judge — priority, scoping, recommendations are its job) but **"cannot decide
  anything *unsafe*."** A wrong judgment errs toward *escalation* or a safe-but-suboptimal choice —
  never across a hard line.

---

## The mechanism: a gated action surface

This is *how* the orchestrator's code-enforcement is real rather than prompted. **The orchestrator
acts through gated `chela` commands, not raw `gh`/shell.** Its tool surface is restricted to:

- `chela dispatch` / `chela review` / **`chela merge`** / **`chela escalate`** — each enforcing its
  slice of the contract, and read-only investigation.
- ⛔ **Not** reachable: raw `gh pr merge`, `pkill`, `git push origin main`, an unrestricted shell.

The seed already existed — **`chela review --approve` refuses on unreadable checks** (unknown ≠
pass) — and that one pattern is now extended across the surface (`chela/contract.py`, CMX-84):
**`chela merge`** enforces the full autonomous gate (base = `dev`, judge `clean`, CI green,
MERGEABLE — all read live, no `--force`), and **`chela escalate`** is the *one* structured way to
reach the human. A wrong LLM opinion cannot reach `gh pr merge` raw, so it cannot merge to `main`.

> A wrong LLM opinion can't wrongly-block (judge); can't wrongly-gate a brief (critic — only facts
> gate); can't take an unsafe action (orchestrator — only the safe envelope is reachable). One
> pattern, three guarantees.

---

## What stays judgment (and why that's safe)

Not everything is mechanizable, and that is fine:

- **Priority** — which of N valid tasks to run next.
- **Brief quality** — is this scoped well (the critic *assists*; final scoping is judgment).
- **Diff reading** — is the design right beyond the mechanical guards.
- **Recommendation forming** — the analysis behind an escalation.

**The safety property:** a wrong decision in *any* of these errs toward **escalation** or a
**safe-but-suboptimal** choice — the code prevents it from ever becoming a *hard-line* violation. So
judgment failures are **recoverable** (a human catches a mediocre pick), never **catastrophic** (the
code blocks the unsafe ones). That asymmetry is the entire point.

---

## Relationship to isolation

Two different blast radii, two different guardrails:

| bounds… | guardrail | buildable now? |
|---|---|---|
| the **decision** blast radius (what it's *allowed* to decide) | **this pattern** — contract as code | ✅ yes (not srt-blocked) |
| the **execution** blast radius (what a raw action can *break*) | **process isolation** (srt) + `memcap` | ⛔ hook-transport blocked |

They are **complementary, not substitutes.** But contract-as-code **reduces the isolation
dependency**: much of what we called "needs isolation before unattended" is actually *decision*-layer
risk that code-enforcement bounds today. Process isolation stays required for the *execution* surface
(agent worktrees, the shell) — but the orchestrator's *authority* is bounded in code now.

**Revised gate on unattended operation:** (a) the escalation contract enforced in code *[this
pattern]* **and** (b) process isolation for the execution surface. Both are required — but (a) is
available now, and it is the larger share of the risk.

---

## Build order (implied)

1. **Judge** — done. The reference.
2. **The contract-as-code core** — **built** (`chela/contract.py`, CMX-84): the mechanical gates
   are extracted into `chela merge` / `chela escalate`, extending the `chela review --approve`
   pattern. `chela merge` refuses unless *base = `dev` (NEVER main/master) AND judge = `clean`
   AND CI green AND MERGEABLE*, reads every GitHub fact live, has **no `--force`**, and logs each
   merge with its justification; `chela escalate` records the decision and pushes it to the human.
3. **Critic** — structural checks (code, can gate) + advisory (LLM); reuses the judge's
   propose-then-adjudicate shape. **v1 built** (`chela/critic.py`, CMX-88): the code half
   (the four-field brief check AND the file-coupling check, both over the task-specific brief)
   wired ADVISORY-ONLY and BRIEFS-ONLY at dispatch — it surfaces a note, gates nothing. The LLM
   judgment half and the gate-on-facts are later slices.
4. **Orchestrator harness** — the persona-loaded session whose action surface *is* the gated
   commands, run **supervised**. **Auto-launch built (CMX-90)** — `chela/personas/autolaunch.py`
   launches it **inbox-woken** (when the decisions inbox has work with nobody live to take it) and
   **attended-lease-gated** (`lease.py`: only while a human's bounded, self-expiring lease is
   active). OFF by default (`CHELA_ORCHESTRATOR`); the lease is the supervision that keeps it
   human-attended without isolation — it decides *when* a supervised orchestrator is woken, never
   *what* it may decide.
5. *(Gated)* **Less-supervised operation** — dropping the attended-lease to run truly unattended,
   once (2)+(4) are solid *and* process isolation covers the execution surface.

---

## Why this is the defensible architecture

Three independently-hand-designed personas would each be "an LLM we hope follows its rules." This
pattern makes them **one proven idea, three times** — the judge's "a wrong reviewer can't wrongly
block," generalized to "a wrong critic can't wrongly gate" and "a wrong orchestrator can't act
unsafely." It is what turns *"embed the personas and eventually run them less-supervised"* from
reckless into **defensible**, and it costs no new invention — only the disciplined extension of the
thing already running in `chela/judge.py`.
