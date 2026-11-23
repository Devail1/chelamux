# Spike: the 5h/7d limits, context and cost from a mod's `session.measure` (CMX-477)

> **Verdict: GO.** Where mods load, the `chela-measure` mod is the primary source for the
> 5h/7d plan limits. It is also a source for context and cost, and the status line stays
> the fallback.
> `session.measure` carries the status line's own figures, including the plan limits that
> no transcript has. The mod occupies nothing the person sees, so it never competes with
> their status line or their organization's. It shipped in this PR as `chela install-mod`.

⚠️ **How this was measured.** This spike ran as a dispatched run. chela's merge gate denies a
dispatched run any nested `claude` session (CMX-190), so **no live session ran the mod
here.** Every fact below comes from one of three places, and each is labelled with its
source:

* **[types]**: the API declaration Claude Code 2.1.330 writes for mod authors
  (`claude-code.d.ts`, `SessionMeasureInput` and the `session.measure` event doc);
* **[binary]**: strings from the 2.1.330 executable;
* **[test]**: `claude plugin test` cases run against the real 2.1.330 engine
  (`chela/mods/chela-measure/hooks/measure.test.ts`, 25 cases). The gate allows
  `claude plugin test` and `claude plugin validate`.

§6 is a 2-minute check that confirms the live behaviour on a real machine.

## 1. The payload

`session.measure`'s input is `SessionMeasureInput` [types]. It holds the same figures as
`$.session.usage()`, without the per-category breakdown:

```ts
{
  context: {
    tokens?: number,   // input tokens of the last response: uncached + cache-written + cache-read
                       //   (the status line's total_input_tokens)
    window: number,    // the model's context window (context_window_size)
    percent?: number,  // tokens / window, whole percent 0..100 (used_percentage)
  },
  rateLimits: Array<{
    kind: string,          // "five_hour" | "seven_day" | a gateway's "spend_limit"
    percentUsed: number,   // 0..100, at most one decimal (past 100 on an exceeded spend limit)
    resetsAt?: string,     // ISO 8601
  }>,                      // EMPTY off a subscription, or before the first reading
  cost?: { usd: number, hasUnknownPrice?: true },   // an estimate, not the bill
  changed: Array<"context" | "rateLimits" | "cost">, // never empty
}
```

The binary confirms the four top-level fields. Its argument checker for the event is
`Wo("session.measure", ["context","rateLimits","cost","changed"], …)` [binary].

A figure the engine does not have is **left out, never zeroed** [types].
* `context.percent` is absent until a response of the live window reports one. That
  happens at a fresh start and right after a compaction (MODS_SPIKE §8.2 saw the same
  through `$.session.usage()`).
* `rateLimits` is `[]` off a subscription (an API key, a cloud provider).
* `cost` is absent where the host keeps no ledger. The CLI always keeps one.

**Mapping to the statusLine payload.** The mod writes the shape
`scripts/cache-statusline.sh` caches, so a single reader serves both:

| `session.measure` | statusLine payload |
|---|---|
| `context.window` | `context_window.context_window_size` |
| `context.tokens` | `context_window.total_input_tokens` |
| `context.percent` | `context_window.used_percentage` (and `remaining_percentage` = 100 − it) |
| `rateLimits[kind].percentUsed` | `rate_limits.<kind>.used_percentage` |
| `rateLimits[kind].resetsAt` (ISO) | `rate_limits.<kind>.resets_at` (epoch seconds, floored) |
| `cost.usd` | `cost.total_cost_usd` |

The mod has no figure for `context_window.current_usage.input_tokens`, the uncached split,
so a mod-written snapshot has no "messages" segment. The status line Mod lost the same
segment (MODS_SPIKE §8.2). The mod has no git `branch` either, which the statusLine script
adds itself.

`chela/mods/chela-measure/hooks/measure-cases.ts` is a recorded input and the file the mod
writes for it. The mod's tests and `tests/test_measure_mod.py` both read that fixture.

## 2. When it fires

* **After each main-thread turn**, when any unit moved since the last measurement. It is
  queued on the turn's event queue right after `turn.complete`. [binary:
  `turnEvents.enqueue("session.measure", …("turn"))`; types: "after each main-thread turn"]
* **When a rate-limit window moves a whole point**, appears or leaves, or the account's
  limit status changes. A subscription on the limit state raises a `"limits"` measurement,
  and that measurement only fires when `changed` includes `rateLimits`. [binary, types]
* **One at a time; a burst folds into one more** [types]. The binary keeps a running
  measurement plus one pending `next`.
* **Per session (per Claude Code process), not global.** Each process measures its own
  usage. The limits are account-wide, but a process only learns them from **its own** API
  responses.
* **An idle session does not fire.** Nothing is measured without a turn or a limit change,
  and a session makes no API calls while idle. Its last snapshot ages out (the reader's
  `CHELA_CACHE_STALE_SECONDS`; `usage.LIMIT_STALE_S` for the bars) exactly as a statusLine
  snapshot does. Any other session's turn refreshes the account-wide limits.
* **Subagent turns** are not main-thread turns [types], so they raise no `"turn"`
  measurement. A limit change they cause can still raise a `"limits"` one (not measured).

## 3. Minimum version and installation

* **Floor: Claude Code 2.1.301**, the first build with mods (MODS_SPIKE §6). This run did
  **not** establish which build first raised `session.measure`, because only 2.1.330 is on
  this host. A build with mods but without the event never calls the hook, so the result
  matches having no mod and the bars fall back. §6's check is the proof on any given machine.
* **Install, with no `settings.json` edit.** Claude Code auto-loads a plugin folder under
  `~/.claude/skills/<name>`. The binary calls these the `@skills-dir` plugins: "plugins
  auto-loaded from .claude/skills/" [binary]. The plugin-authoring reference says the same
  ("a plugin auto-loaded from a skills folder (`~/.claude/skills/<name>`)"). That scope is
  **user scope**, for every project. `chela install-mod`:
  * **copies** the packaged mod (`chela/mods/chela-measure`, shipped in the wheel) to
    `$CLAUDE_CONFIG_DIR/skills/chela-measure`, defaulting to `~/.claude/skills/chela-measure`.
    It copies rather than links because a `uvx` install lives in a cache that
    `uv cache prune` deletes, the same reason `install-statusline --write` copies its script;
  * writes a `chela.json` beside it that names this chela's `CHELA_DIR`. The mod looks for
    its directory in this order: `$CHELA_DIR` in the session's environment, then
    `chela.json`, then `~/.chela`;
  * refuses to replace a folder that is not this mod, and replaces its own earlier copy
    whole. `chela install-mod --remove` takes it out.
* New sessions load it. A running session loads it after `/reload-plugins`.
* Project scope (`<repo>/.claude/skills/<name>`) also auto-loads, but only once the
  workspace is trusted, and only for that repo. chela does not use it.

## 4. What an organization can block, and how chela tells

From the 2.1.330 binary's policy code and the types [binary, types]:

| Managed setting | Effect on chela-measure |
|---|---|
| `strictKnownMarketplaces` (any allowlist) | **blocks the `~/.claude/skills` scan** unless the list holds `{"source": "skills-dir"}` |
| `blockedMarketplaces` containing `{"source": "skills-dir"}` | turns the scan off |
| `allowManagedHooksOnly` | "a `user`-tier plugin loads no hooks": the mod loads, but its hook never runs |
| `disableAllHooks` (managed) | no hooks module runs at all |
| `forceHotReloadPrompt` | concerns only mods written into a session's dev-mods folder, not this install |

When the scan is blocked, Claude Code logs: *"Plugins from …/ are blocked by your
organization's managed settings (strictKnownMarketplaces or blockedMarketplaces). Ask your
administrator to add {"source":"skills-dir"} to strictKnownMarketplaces, or remove it from
blockedMarketplaces."* [binary]

**How chela detects it: by the output, not by parsing policy.** Managed settings come from
several places (a file, MDM on macOS, server-managed), and chela cannot read all of them.
`chela doctor`'s `usage.sources` fact reports:
* whether the mod is installed, and whether its `chela.json` names this `CHELA_DIR`;
* the 5h/7d bars **with the source of each value**;
* every session that has a fresh snapshot, with the producer that feeds it.

An installed mod with no `mod` row after a session's turn means a policy blocked the scan or
its hooks. `claude --debug` then names the reason.

**With mods blocked:** context still comes from the transcript. Token usage still comes from
the transcript-based usage view, which has no $ figure. The **5h/7d bars read "unknown"** unless
the statusLine hook is installed. They never read 0%.

## 5. Coexisting with other user mods

The requirement: chela's mod must run beside Liav's own mod (a Claude account switcher)
without either one affecting the other.

* **Its own name and namespace.** The plugin is `chela-measure`. It uses no `$.state` and
  no `$.store`, and it only writes `$CHELA_DIR/context/mod/<session_id>.json`. It hooks
  `session.measure` and nothing else, draws nothing and registers no command.
* **It passes the event on first.** The hook calls `await next(e)` before its own write, and
  the write never throws. A mod beneath chela's therefore always gets the event, and
  chela's failure cannot cost it anything.
* **Measured [test]:**
  * An inline "account switcher" mod in the `prepend`, `user` or `append` tier, which
    records each `session.measure` it sees and passes it on: **both mods receive the same
    event, and both write their own file**. The engine's answer comes back unchanged.
  * The one case chela cannot fix from its side: a mod **above** chela's that answers
    without calling `next(e)` keeps the event from everything beneath it. In the test kit a
    `prepend` mod like that starves chela's, while an `append` one does not. Inside the kit,
    chela's mod also runs above a `user`-tier inline mod. In a real session the order
    within one tier is the load order, which this run did not measure. `session.measure` is
    an observe event ("Observe; `next(e)` echoes `{ changed }`"), so a well-behaved mod
    always calls `next`.

## 6. The 2-minute check on the work Mac

This check confirms live loading, and that the account switcher and chela's mod both work.
The steps assume a chela release that has `chela install-mod`: run `uv tool upgrade chelamux` first.

```bash
chela install-mod
# Start a NEW claude session anywhere (the switcher loads as usual), send one prompt,
# wait for the reply, then in another terminal:
ls -l "${CHELA_DIR:-$HOME/.chela}/context/mod/"
chela doctor | grep -A3 -E 'chela-measure|limits:|session\(s\) fed'
```

**GO** looks like this:
* `context/mod/` holds a `<session-id>.json` written at that turn's time;
* doctor prints `limits: 5h N% (mod) · 7d M% (mod)` on a subscription account (`(statusline)`
  instead means the statusLine file was newer, which is fine), and `… fed: 1 by the mod …`;
* the account switcher behaves as before.

**NO-GO** looks like this: no file appears after the turn. Find out why with:

```bash
claude --debug-file /tmp/cm.log -p "hi" >/dev/null; grep -iE 'chela-measure|skills-dir|blocked by your organization' /tmp/cm.log
```

A "blocked by your organization's managed settings" line means §4's
`strictKnownMarketplaces`/`blockedMarketplaces`. A line saying the hooks were skipped means
`allowManagedHooksOnly`.

## 7. What this run did not cover

* A live session. The payload, the firing rules and the policy facts come from 2.1.330's
  types and binary. Two-mod delivery comes from the engine's own test kit, not a session.
  §6 closes this gap.
* The first Claude Code build that has `session.measure`.
* Mod load order within one tier in a real session (§5).
* Whether a `"limits"` measurement fires for a change a subagent's request caused.
