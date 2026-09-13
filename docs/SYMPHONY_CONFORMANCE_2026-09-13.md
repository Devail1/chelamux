# Symphony conformance — a sized gap list

**Date:** 2026-09-13 · **Against:** chela `58c88c5` (v0.12.1) · **Spec:** [openai/symphony](https://github.com/openai/symphony) `SPEC.md`, "Draft v1 (language-agnostic)", read in full (2312 lines).

This is a **read-and-report** document. Nothing here has been implemented. It exists so the
divergences can be chosen, not discovered.

README's Credits section already says the work-item dispatcher is an adaptation of the Symphony
pattern, and `chela/workflow.py:124-146` + `chela/dispatcher.py:3717` implement SPEC 6.2/6.3
(dynamic reload) by name. The question this document answers is what *else* is worth inheriting.

## Summary

**The headline is that the adapter layer already exists.** `chela/sources/__init__.py:28-36` is a
tracker-adapter registry keyed on `tracker.kind`, with two working adapters — `markdown` and
`gh_issues` — and `gh_issues` is already integrated end-to-end (dispatcher claim, strike, brief,
dashboard, `chela doctor`, the setup skill, its own test file). So "collapse the two registries"
is **not a rewrite of the claim path**. It is a config flip plus four closable gaps.

What the spec exposes instead is a **different, larger hole**: chela implements one of SPEC 11.1's
two REQUIRED adapter operations. The missing one (`fetch_issues_by_ids`) is exactly why a failed
tracker read can mark live work `done`.

| # | Gap | Kind | Size | Payoff/risk |
|---|-----|------|------|-------------|
| G1 | No ID-refresh op; **absence is read as terminal** | 🔴 DEFECT | ~1 day | **highest** |
| G2 | chelamux's own workflow still runs the `markdown` adapter | UNEXAMINED | ~2–3 days, gated on G1 | high |
| G3 | `gh_issues` dispatches **newest-first** (LIFO) | 🔴 DEFECT | ~2h | high |
| G4 | `gh_issues` never fetches the issue **body** ⇒ no brief | 🔴 DEFECT | ~3h | high |
| G5 | `markdown` task id is derived from the title | 🔴 DEFECT | ~1 day + format break | low — G2 dissolves it |
| G6 | Trust/approval posture is not documented (SPEC MUST) | UNEXAMINED | ~2h | good, cheap |
| G7 | Missing `after_run` / `before_remove` / `hooks.timeout_ms` | UNEXAMINED | ~4h | low |
| G8 | No per-state concurrency; the global count spans workflows | ✅ DELIBERATE | — | **don't adopt** |
| G9 | No exponential backoff | ✅ DELIBERATE | — | **don't adopt** |
| G10 | The judge has no Symphony counterpart | ✅ DELIBERATE | — | **don't adopt** |
| N1 | Two reconcile authorities (tracker state **and** PR state) | ✅ DELIBERATE | — | **don't adopt; close #491** |

**Verdict on the three items proposed:** item 1 survives but shrinks to an adapter-local defect
whose cure is item 3, not a fix. **Item 2 dies** — twice over. Item 3 survives, but re-sized down
from "a week+ rewrite" to "2–3 days", and **re-ordered**: G1 must land *before* it, not after.

---

## G1 — SPEC 11.1: only one of the two REQUIRED adapter operations exists 🔴 DEFECT

> **SPEC 11.1:** "An implementation MUST support these adapter operations: 1. `fetch_issues_by_states(state_names)` … 2. `fetch_issues_by_ids(issue_ids)` — Return current normalized issue snapshots for the supplied opaque dispatch IDs. Used for active-run reconciliation and stale-dispatch revalidation."

> **SPEC 11.1:** "A state-list call MAY omit an individually malformed provider record because it was never safe to dispatch, and SHOULD log that omission. **An ID-refresh call MUST fail instead of silently omitting a malformed requested record, because omission is meaningful.**"

> **SPEC 8.4:** "ID refresh avoids treating a terminal, non-active, or newly unroutable issue as merely absent."

> **SPEC 11.4:** "Orchestrator behavior on tracker errors: Candidate fetch failure: log and skip dispatch for this tick. Running-state refresh failure: log and keep active workers running."

**What chela does instead.** The adapter protocol is a single method, `list_open_tasks()`
(`chela/sources/markdown.py:29`, `chela/sources/gh_issues.py:140`). Reconciliation derives
terminality *by subtraction*: `open_ids = {t.id for t in open_tasks}` (`dispatcher.py:3753-3754`),
then `if row["task_id"] not in open_ids …` → `status='done'` (`dispatcher.py:4018-4044`).

**Why this is a defect and not a style choice.** Both adapters return `[]` for a *failed read*,
indistinguishable from an *empty queue*:

- `gh_issues.py:147` (repo unresolvable), `:153` (config error), `:171` (`gh` missing/timeout),
  `:177` (non-zero exit — auth expiry, rate limit, 500), `:182` (bad JSON) — every one returns `[]`.
- `markdown.py:32` — a missing tracker file returns `[]`. chelamux's `TODO.md` is **gitignored**
  (`dispatcher.py:780-788`), so a `git clean -xdf` produces exactly this.

On the next tick `open_ids` is empty, and **every** `awaiting_review` / `changes_requested` /
`needs_human` row is flipped to `done`, its tmux window killed, its worktree deleted
(`_cleanup_worktree_on_done`, `dispatcher.py:3505`), and `merged_in_tick` incremented — which fires
`hooks.after_done`, a **"shipped" event for work that did not ship**.

Recoverable (the branch is deliberately preserved, `dispatcher.py:3516-3519`, and the PR is still on
GitHub — `chela reopen` / `adopt_pr` can rebuild), but it is silent, and it is a lie in the Done lane.

⭐ **chela's own code already knows this.** `dispatcher.py:4046-4053`: *"claimed/running, no review
state: 'left the tracker' is NOT proof of a merge (cmx-100) … Gate `done` on completion evidence."*
That reasoning was applied to the `claimed`/`running` branch and **not** to the review-state branch
directly above it. The spec's answer is the general form of cmx-100's answer.

**Size: ~1 day.** Add a second adapter method (`fetch_by_ids(ids) -> list[Task] | None`, `None`
meaning *the read failed*, distinct from `[]` meaning *none of these are open*); implement it for
both adapters (`gh issue view --json state` batched, or `gh issue list --search`; a file re-read for
markdown); make `tick()` skip the absence-implies-terminal branch whenever the read failed. Touches
`chela/sources/*` (3 files) and one branch of `tick()`.

**What breaks if adopted:** nothing in behaviour — it only ever *withholds* a terminal transition.
The cost is that a genuinely-deleted tracker line now takes one extra confirmed read to reconcile.
The mutation-testing guard is easy and real: *corrupt the adapter to return `[]` on a read failure
⇒ a live `awaiting_review` row goes `done` ⇒ RED.*

---

## G2 — SPEC 11: run chelamux's own workflow on the `gh_issues` adapter (item 3)

> **SPEC 11 preamble:** "The issue tracker boundary is deliberately small: a portable read kernel for scheduling plus OPTIONAL provider-native agent tools."

> **SPEC 11.2:** "Each adapter owns: … choosing a stable dispatch identity and preserving any distinct underlying IDs in `native_ref`; deriving `dispatchable` from provider-specific routing rules."

**What chela does instead.** Nothing structural — the boundary is already there. `get_source(wf)`
(`chela/sources/__init__.py:28`) selects on `tracker.kind`. What is *configured* is the split:
`WORKFLOW.md:14-16` says `kind: markdown, path: TODO.md`, while the actual backlog lives in 12 open
GitHub issues. Measured today: `TODO.md` is 336 lines, **43 struck against 1 open**.

**Kind: UNEXAMINED.** The `markdown` choice predates `gh_issues` existing; nothing recorded says it
was re-decided afterward.

**The four gaps that must close for this workflow to actually run on `gh_issues`:**

1. **No issue body (G4)** — see below. Without it the dispatch brief becomes a URL.
2. **LIFO order (G3)** — see below.
3. **No `depends:` edges.** `markdown.py:271-283` resolves `<!-- depends: "…" -->` into blocking
   ids, enforced in `_ready` (`dispatcher.py:572`). `gh_issues` has no equivalent
   (`runtime_truth.py:2818`: *"e.g. gh_issues — no notion of depends: at all"*). SPEC 4.1.1 has
   `blocked_by`, but calls it *"Best-effort provider metadata"* — it does not carry the fail-closed
   semantics `_ready` deliberately has. **~1 day** to add a body-marker or label equivalent, or
   accept the loss.
4. **G1 must land first.** On `markdown`, the empty-read defect needs a deleted file. On
   `gh_issues` it needs one `gh` rate-limit or auth blip on *any* 60s tick. Migrating before G1
   converts a rare failure into a routine one.

**⛔ What breaks / what is lost — the part that is not an engineering decision:**

- **Every dispatch brief becomes public.** `TODO.md` is gitignored *on purpose* — a local,
  per-install queue (`dispatcher.py:780-786`). Its briefs carry OBJECTIVE / BOUNDARIES / GUARDS /
  VERIFY, and reference this machine and neighbouring projects. GitHub issues on a **public repo**
  publish all of it. Liav already writes full briefs into issues (#482's body is one), so this is
  partly already true — but it becomes *unconditionally* true.
- **The human hop is relocated, not removed.** `require_label` is a **security control**, not a
  convenience (`gh_issues.py:50-64`): on a public repo, applying a label needs write/triage
  permission, and that is the only thing standing between a stranger's issue and an autonomous
  agent on this box. So every dispatch is still preceded by a human act. It is a much cheaper act
  (one label vs. writing a brief into a second file), but claiming the hop disappears is wrong.
  Prerequisite: the `ready-for-agent` label does not exist on this repo yet (checked).
- **Queue ordering by hand is lost.** Reordering `TODO.md` reorders the queue. GitHub has no
  ordering primitive; you get `created_at` (see G3) plus, if you build it, a priority label.
- **`hold` + fetch-then-claim change meaning.** `_claim_order` (`dispatcher.py:485`) re-reads the
  queue from `origin/<base>` at the instant of claiming; `gh_issues` is live-read and therefore
  already claim-fresh (`dispatcher.py:505`). The `chela hold` mechanism is unaffected.

**On issue #482** ("the tracker is 97% struck work"): a `gh_issues` tracker **dissolves** it for
this workflow — a closed issue leaves the active query, so struck lines cannot accumulate. It does
**not close the issue**: #482 asks for a Work-board collapse affordance, which stays valid for every
chela install still on `markdown`. Recording it as "fixed by migration" would be wrong.

**Size: ~2–3 days total**, of which ~1 is G1 (which is worth doing regardless) and ~1 is the
`depends:` decision. The config flip itself is minutes.

**Is "gh_issues first, TODO.md as a second adapter" a trap?** No — it is the shipped design, and
both are registered simultaneously with per-workflow selection. The trap is the *current* state:
two registries for the **same** work with a human copy in between. Per-workflow selection is fine;
per-workflow *duplication* is what costs.

---

## G3 — SPEC 8.2: `gh_issues` dispatches newest-first 🔴 DEFECT

> **SPEC 8.2:** "Sorting order (stable intent): 1. `priority` ascending for values `1..4` … 2. `created_at` oldest first; null sorts last 3. `identifier` lexicographic tie-breaker"

**What chela does instead.** `gh_issues.py:154-158` calls `gh issue list … --limit 200` and appends
in response order. **Measured on this repo today:** `496, 494, 491, 490, 489, 488, 487, 486` —
`gh`'s default is created-descending. Nothing re-sorts: `_claim_order` preserves order and `_ready`
only filters. So a `gh_issues` workflow is a **LIFO stack** — the newest issue is always claimed
first and the oldest starves.

For `markdown` this is not a defect: file order *is* the operator's stated priority, and that is a
deliberate, working design.

**Size: ~2h.** Sort by `createdAt` ascending in `GhIssuesSource.list_open_tasks`; add `createdAt` to
the `--json` field list. Optionally map a `priority:N` label to SPEC 8.2's `1..4` bucket.

**What breaks:** nothing — it has never run as the configured tracker here.

---

## G4 — SPEC 4.1.1 / 12.1: `gh_issues` drops the issue body 🔴 DEFECT

> **SPEC 4.1.1:** "`description` (string or null)" — a REQUIRED field of the normalized Issue.
> **SPEC 11.3:** "Every listed field MUST be present in the normalized record."

**What chela does instead.** `gh_issues.py:157` requests `number,title,url,labels,author` — no
`body`. `Task.body` is therefore always `None` (`sources/__init__.py:16-19` documents this), and
`_task_brief` (`dispatcher.py:4756-4765`) falls back to `task.raw` — **the issue URL**. So
`runs.brief`, the task-detail modal's left pane, and the agent's seed all degrade to a link.

This matters more here than it would elsewhere: the standing review rule is to benchmark a
dispatched PR **against its brief's required GUARDS/OBJECTIVEs**. A brief that is a URL cannot be
benchmarked against, and the judge cannot see a guard that was never written down.

**Size: ~3h.** Add `body` to the `--json` list; map it to `Task.body`. `_task_brief` already prefers
`body`. One test.

**What breaks:** nothing. Note the body arrives as untrusted public markdown on a public repo — the
`require_label` gate is what makes it trustworthy, which is another reason that gate is load-bearing.

---

## G5 — SPEC 4.1.1: the `markdown` id is minted from the human text 🔴 DEFECT (but don't fix it here)

> **SPEC 4.1.1:** "`id` (string) — REQUIRED stable dispatch identity within the configured tracker scope. **Opaque to the orchestrator.**"
> **SPEC 4.1.1:** "`identifier` (string) — REQUIRED human-readable ticket key (example: `ABC-123`). MUST be unique within the configured tracker scope because it names workspaces and operator-facing routes."
> **SPEC 4.2:** "`Issue ID` — Use for tracker refresh calls and internal map keys. Treat it as an opaque dispatch identity."

**Confirmed: chela mints the id from the row's first line.** `markdown.py:291-297`:
`_title_id(filename, title) = sha1(f"{filename}\x00{title.strip()}")[:12]`. Editing a task's title
therefore re-mints its id — the origin of the standing rule *"⛔ never edit a task's first line
mid-run"*. The spec's domain model does structurally remove that landmine.

⭐ **But the useful framing is sharper than "chela lacks a field".** chela has *both* fields — they
are just **inverted** relative to the spec:

| | SPEC 4.1.1 | chela |
|---|---|---|
| `id` (stable, opaque) | adapter-supplied | `markdown`: **derived from human text** (unstable). `gh_issues`: `sha1("gh:{repo}#{n}")` — **already conformant** |
| `identifier` (human-readable) | adapter-supplied, unique in tracker scope | minted from the **runs table**: `{project_key}-{task_number}` = `MAX(task_number)+1` (`worktree.py:27-38`), naming the branch and the tmux window |

So the spec's `id` is unstable on one adapter, and the spec's `identifier` is minted from
orchestrator state rather than the tracker — and `worktree.py:35-38` already flags that the DB "does
not remember forever", so `task_number` can be re-issued after retention pruning.

**⛔ Why fixing `markdown` in place is the wrong move.** To give a checkbox row a stable id you must
write the id *into the line* (`- [ ] <!-- id: abc123 --> …`), which (a) changes the file format
operators hand-edit, (b) invalidates every existing `TODO.md` and every in-flight worktree/branch
keyed on the old hash, and (c) breaks `depends:` edges, which resolve by hashing the *other
bullet's title* (`_resolve_depends`, `markdown.py:271`) — the entire edge mechanism is title-keyed
by design. **Size if done anyway: ~1 day plus a format break.**

**Recommendation: don't.** `gh_issues._task_id` is already spec-shaped and its docstring names this
exact defect (*"Unlike the markdown source (where editing the line text mints a new task_id),
renaming an issue keeps the same task_id → same branch → same worktree"*). **Item 1 is bought by
doing G2, not by patching G5.**

---

## G6 — SPEC 1 / 10.5: the trust posture is a documented MUST, and is undocumented

> **SPEC 10.5:** "**Approval, sandbox, and user-input behavior is implementation-defined.**"
> **SPEC 10.5:** "Each implementation MUST document its chosen approval, sandbox, and operator-confirmation posture."
> **SPEC 1:** "Implementations are expected to document their trust and safety posture explicitly. This specification does not require a single approval, sandbox, or operator-confirmation policy."

**Verified: the spec really does punt on the review model** — and it punts harder than the brief
assumed, because it punts in §1 as well as §10.5. There is **no** counterpart anywhere in 2312 lines
to chela's judge (mutation-testing a PR before merge), to `request_changes`/rework rounds, or to the
escalation-to-human path. Symphony's nearest analogue is §11.5's *"Workflow-specific success often
means 'reached the next handoff state' (for example `Human Review`)"* — a handoff, not a review.

**⛔ This confirms the recommendation: inheriting the queue model must not drag a review model with
it, because there is no review model in the spec to drag.**

But the same clause is a cheap win: it is a **MUST**, and chela satisfies it *almost* by accident —
`resolve_agent_cmd` precedence, `--permission-mode auto`, `workspace_escape`'s fence, the
`require_label` gate, and the "everything committed here is published" posture are all real and all
written down *somewhere*. They are not in one place a conformance reader can cite.

**Size: ~2h**, one doc page. **What breaks:** nothing. It is the only item here that lets chela
*claim* conformance rather than move toward it.

---

## G7 — SPEC 9.4 / 18.1: two lifecycle hooks and the hook timeout are missing

> **SPEC 9.4:** "Supported hooks: `hooks.after_create`, `hooks.before_run`, `hooks.after_run`, `hooks.before_remove`" · "Hook timeout uses `hooks.timeout_ms`; default: `60000 ms`."

chela has `hooks.after_create` (`dispatcher.py:4995`), `hooks.before_run` (`:5003`), and
`hooks.after_done` (`:2288`) — but `after_done` fires on **merge**, not on attempt completion, so it
is not SPEC's `after_run`. No `before_remove`; no `hooks.timeout_ms`.

**Kind: UNEXAMINED.** **Size: ~4h.** **Payoff: low** — nothing today wants either hook.
**What breaks:** nothing, they are additive.

---

## G8–G10 + N1 — the deliberate divergences. ⛔ Do not adopt.

### G8 — Concurrency (SPEC 8.3) ✅ DELIBERATE

> **SPEC 8.3:** "Global limit: `available_slots = max(max_concurrent_agents - running_count, 0)`. Per-state limit: `max_concurrent_agents_by_state[state]` if present."

chela has one global count and **no per-state limit** — and its count is deliberately global *across
workflows*: `dispatcher.py:4546-4552` counts `status IN ACTIVE_STATUSES` with **no `workflow_path`
filter**, and `WORKFLOW.md:26-29` documents exactly that (*"the dispatcher's active-count is GLOBAL
across workflows, so this also caps total box-wide agents; lean-alpha stays at max:1 so two heavy
LEAN backtests never run in parallel (memcap/OOM)"*). The constraint being enforced is **one
machine's RAM**, which is not a per-state property. Per-state limits would be a strictly weaker
guarantee here. `JUDGE_MAX_CONCURRENT=1` is the same chosen constraint.

### G9 — Retry and backoff (SPEC 8.4) ✅ DELIBERATE

> **SPEC 8.4:** "Failure-driven retries use `delay = min(10000 * 2^(attempt - 1), agent.max_retry_backoff_ms)`."

chela uses attempt caps (`MAX_ATTEMPTS`), a rework cap (`CHELA_MAX_REWORKS`) and **escalation to a
human** (`_escalate`, `dispatcher.py:5233`) instead of a backoff curve. The difference is in what
fails: Symphony's failure mode is a crashed subprocess, which a later retry can fix. chela's is a
**PR that failed review**, which a faster retry cannot. Backoff answers the wrong question.

### G10 — The review model (SPEC 10.5) ✅ DELIBERATE — see G6.

### N1 — Reconciliation and the two authorities (SPEC 8.5) ✅ DELIBERATE. **Item 2 dies.**

> **SPEC 8.5 Part B:** "For each running issue: If tracker state is terminal: terminate worker and clean workspace. If tracker state is still active and routable: update the in-memory issue snapshot."

The claim under test was that SPEC 8.5 removes the need for `RECONCILE_MERGE_STATUSES`
(`dispatcher.py:95-108`) and thereby closes #491. **It fails on both halves.**

**(a) #491 is already fixed.** `1e5ab41` — *"CMX-360: reconcile a merged PR whose run row is still
`running` (#491)"* — landed in **v0.12.1**, the current HEAD. `RECONCILE_MERGE_STATUSES_WITH_RUNNING`
is live at `dispatcher.py:3919`, guarded by `pr_state == "merged"` so a `running` row whose PR is
*not* merged is untouched. **The GitHub issue is still open only because nothing closed it** — the
work was dispatched from a `TODO.md` line, not from `Closes #491`. That is itself a small, concrete
piece of evidence for G2.

**(b) The spec dodges chela's case; it does not solve it.** Symphony has **no PR concept at all**.
It has one authority because SPEC 11.5 hands ticket mutation to the agent:

> **SPEC 11.5:** "Ticket mutations (state transitions, comments, attachments, PR metadata) are typically handled by the coding agent through the selected adapter's provider-native tools." · "The service remains a scheduler/runner and tracker reader."

chela's second signal is precisely the **replacement for that trust**. `dispatcher.py:3879-3885`:
*"agents don't touch the tracker any more, so an `awaiting_review` row would otherwise sit there
forever waiting for a line that only we will ever strike"*, and `_strike_merged_tasks`
(`dispatcher.py:756-767`) says why: two writers on one file conflicted on every PR, **and** the
checkbox now means *merged* rather than *the agent believed it was finished* — "which is strictly
more truthful". Adopting SPEC 8.5's single authority means giving the agent back the power to
declare itself done. **That is a regression dressed as conformance.**

⚠️ Note the knock-on: chela is *also* a tracker **writer** (`_strike_merged_tasks`), which SPEC 11.5
explicitly says the orchestrator need not be. That divergence is deliberate and load-bearing for
the same reason.

**Action: none, except close #491** and record this divergence.

---

## Where chela EXCEEDS the spec

> **SPEC 7.4:** "Restart recovery is tracker-driven and filesystem-driven (without a durable orchestrator DB)."
> **SPEC 18.2 (RECOMMENDED, not required):** "TODO: Persist retry queue and session metadata across process restarts."

chela's orchestrator state is a durable SQLite `runs` table, not in-memory maps
(`SPEC 4.1.8`) — so what Symphony lists as an unfinished TODO is chela's baseline. Worth saying out
loud before adopting anything from §7.4/§8.4, which are written *around* the absence of that DB.

---

## What I would do FIRST, and why

**1. G1 — the ID-refresh operation (~1 day).** It is the only item on this list that is a live
correctness bug in shipped code: a failed tracker read silently marks live, unmerged work `done`,
kills its window, deletes its worktree and fires the `after_done` "shipped" hook. It is a
prerequisite for G2 — migrating to a network-backed tracker without it converts a rare failure into
a routine one. And it is the item with the strongest evidence: the spec states the rule twice, and
chela's own cmx-100 comment already reached the same conclusion for the neighbouring branch.

**2. G3 + G4 (~half a day together).** Both are small, both are pure gain, and both must be true
before anyone can judge whether a `gh_issues` workflow is actually better. Do them next so the G2
decision is made on a fair comparison rather than on a crippled adapter.

**3. Then decide G2 — as a positioning call, not an engineering one.** The engineering is ~a day
once G1/G3/G4 are in. The real question is whether every dispatch brief should be public, and
whether losing hand-ordering and `depends:` is acceptable. That is Liav's to answer, and the
honest framing is: *gh_issues does not remove the human hop, it makes the hop one label instead of
one hand-copied brief.*

**4. G6 (~2h) whenever convenient.** Cheap, satisfies a spec MUST, and is the only thing here that
converts existing behaviour into a conformance claim.

**Adopt nothing else.** G5 is dissolved by G2 and actively harmful to fix in place. G7 is real but
nothing wants it. G8, G9, G10 and N1 are deliberate, correctly reasoned, and each would trade a
property chela has for one it does not want.
