// ---------------------------------------------------------------------------
// THE DECISIONS LOG'S MODEL: what a log row IS (its class) and how the log is read (the
// drain). Pure: no DOM, no fetch. It was static/js/feedmodel.js, the Feed view's model; the
// Feed's lane grouping left with the view (CMX-279), and CMX-228 (CMX-212 P07) moved what
// the Decisions inbox still reads here.
//
// ⚠️ COLOUR IS NEVER THE SIGNAL. Every class carries a GLYPH and a WORD (`◆ gate`,
// `■ lifecycle`, `● run`, `× denied`, `▸ prompt`); the hue (Okabe-Ito, in the CSS)
// is decoration on top of a label that already reads without it.
// ---------------------------------------------------------------------------

/** One /api/log event (chela/event_log.py). */
export interface LogEvent {
    seq: number;
    ts?: number;
    type: string;
    wid?: string | null;
    summary?: string;
    payload?: Record<string, unknown> | null;
}

/** What /api/log says when the cursor no longer lines up with the log (event_log.read). */
export interface LogGap {
    reason?: string;
}

interface LogBatch {
    events?: LogEvent[];
    next_seq: number;
    last_seq: number;
    boot_id: string;
    gap?: LogGap | null;
}

export interface Drained {
    events: LogEvent[];
    cursor: number | null;
    boot: string | null;
    gap: LogGap | null;
    cleared: boolean;
}

export type ClassId = 'gate' | 'denied' | 'run' | 'lifecycle' | 'prompt' | 'tool' | 'other';

// The classes a row can belong to. `glyph` + `word` are the identity; the colour
// var is applied by the CSS and carries no information of its own.
export const CLASSES: Record<ClassId, { glyph: string; word: string; label: string }> = {
    gate: { glyph: '◆', word: 'gate', label: 'Gates' },
    denied: { glyph: '×', word: 'denied', label: 'Denied' },
    run: { glyph: '●', word: 'run', label: 'Runs' },
    lifecycle: { glyph: '■', word: 'lifecycle', label: 'Lifecycle' },
    prompt: { glyph: '▸', word: 'prompt', label: 'Prompts' },
    tool: { glyph: '⋯', word: 'tool', label: 'Tool calls' },
    other: { glyph: '·', word: 'event', label: 'Other' },
};

// type → class. The hook events are namespaced (`hook.*` = an agent told us this);
// the bare ones are chela's own bookkeeping (chela/inbox.py, chela/main.py).
const TYPE_CLASS: Record<string, ClassId> = {
    'hook.permission_request': 'gate',      // the agent is BLOCKED, asking for you
    'hook.elicitation': 'gate',
    blocked: 'gate',                        // inbox: "is BLOCKED on a prompt"
    'hook.permission_denied': 'denied',
    run_review: 'run',
    run_failed: 'run',
    // The rework loop's two states (CMX-68). Without these they fall through to `other`,
    // which renders the ONE event that means "a human must look at this run" as an
    // anonymous `·` — and drops it out of the Runs filter, where a run event belongs.
    run_changes_requested: 'run',
    run_needs_human: 'run',
    // CMX-197: the judge's own verdict on a run that's still sitting in `awaiting_review`
    // (a BLOCKED verdict already lands as `run_changes_requested` above — this is the
    // other two: settled clean, or settled cannot-verify). Without this it falls to
    // `other` and the one push that says "this PR is mergeable" reads as an anonymous `·`.
    run_judge_clean: 'run',
    run_judge_cannot_verify: 'run',
    // CMX-239: the CAS-refused race for a BLOCKING verdict — the run left review before
    // `request_changes` could send it back, so it never reaches `run_changes_requested`
    // above. Same reasoning as `run_judge_cannot_verify`: without this it falls to `other`
    // and the one push that says "a guard survived corruption, possibly already shipped"
    // reads as an anonymous `·`.
    run_judge_blocked_race: 'run',
    // CMX-358 (#480): the PR merged before a judge ever ran for it — same reasoning as the
    // two above, without this it falls to `other` and the one push that says "this shipped
    // with NO verdict at all" reads as an anonymous `·`.
    run_unjudged_merged: 'run',
    // CMX-86: the orchestrator inbox's new run notices — same reasoning, a run event belongs
    // in the Runs filter, never as an anonymous `·`. A lapsed/released hold is lifecycle.
    run_judge_not_started: 'run',
    run_reopened: 'run',
    run_was_closed: 'run',
    run_ci_red: 'run',
    run_ci_flake: 'run',
    run_pr_conflicting: 'run',
    run_stuck: 'run',
    run_marker_unread: 'run',
    run_selfcheck_long: 'run',
    // CMX-318: a park (`chela park`) that outlived its --until or the max.
    run_park_overdue: 'run',
    dispatch_hold_expired: 'lifecycle',
    dispatch_hold_released: 'lifecycle',
    // The inbox cannot reach the orchestrator (CMX-77): its queue is addressed to a window
    // id a dead tmux server issued, or to a session that has exited. It is a `gate` because
    // that is exactly what it is — work is stuck until a HUMAN (or the orchestrator's next
    // `chela watch`) acts, and the whole cost of the 2026-07-14 outage was that this state
    // had no surface at all. It must not fall through to an anonymous `·`.
    inbox_undeliverable: 'gate',
    // The recovery (CMX-82): the inbox re-resolved its rotted address from the orchestrator's
    // session identity and the held queue is flowing again — a lifecycle event, the good-news
    // counterpart to inbox_undeliverable, done with no human in the loop.
    inbox_self_healed: 'lifecycle',
    // CMX-394: the orchestrator pin changed hands without that window's own `chela watch`
    // (a self-heal, `chela restore`, the dashboard) — or a watch took it from a live one.
    'orchestrator.moved': 'lifecycle',
    watch_epoch_lost: 'lifecycle',          // the watched agent died with the tmux server
    'hook.user_prompt_submit': 'prompt',
    'hook.pre_tool_use': 'tool',
    'hook.post_tool_use': 'tool',
    'hook.session_start': 'lifecycle',
    'hook.session_end': 'lifecycle',
    'hook.stop': 'lifecycle',
    'hook.subagent_start': 'lifecycle',
    'hook.subagent_stop': 'lifecycle',
    'hook.pre_compact': 'lifecycle',
    'hook.post_compact': 'lifecycle',
    'hook.notification': 'lifecycle',
    daemon_start: 'lifecycle',
    finished: 'lifecycle',
    died: 'lifecycle',
    gone_unknown: 'lifecycle',
    completed_gone: 'lifecycle',
};

// An unknown type is `other`. A log is allowed to grow a type the UI has never heard of
// (`chela events emit --type note`), and the safe default for "I do not recognise this" is
// to SHOW it, not to swallow it.
export function classOf(type: string): ClassId {
    return (Object.hasOwn(TYPE_CLASS, type) && TYPE_CLASS[type]) || 'other';
}

// --- the cursor: a bounded read that RESUMES, and never SKIPS -----------------
//
// /api/log hands back the `limit` OLDEST events after the cursor, so on a busy log one
// call does not reach the tail — a reader drains until it does. The whole contract is
// one line of this loop: the cursor advances to `next_seq`, NEVER to `last_seq`.
//
// `last_seq` is the LOG's tail. When a read was truncated by `limit` it sits far past
// the last event we were actually HANDED, so resuming from it drops every event in
// between — invisibly, which is the exact hole event_log's `next_seq`/`gap` design
// exists to prevent (`event_log.read()`: "a bounded read is resumable and can never skip
// an event the caller has not seen"). `last_seq` has one legitimate use, and only one:
// it says whether we have reached the tail yet, i.e. whether to keep draining.
//
// This lives here, DOM-free, because that is what makes the rule provable: the drain is
// tested against a fake log in tests/feed.test.ts, where a resume-from-last_seq reader
// is run over the same log and SHOWN to skip. (It used to be "asserted" by grepping
// feed.js for the string `batch.last_seq` — which failed the correct code, since the
// tail test legitimately reads it. A grep tests spelling; this tests behaviour.)
//
// `fetchBatch({after_seq, after_boot, limit})` is injected — so no fetch in here either.
export async function drainLog(
    fetchBatch: (q: { after_seq: number | null; after_boot: string | null; limit: number }) => Promise<unknown>,
    { cursor = null, boot = null, limit, maxFetches }: { cursor?: number | null; boot?: string | null; limit: number; maxFetches: number },
): Promise<Drained> {
    let events: LogEvent[] = [];
    let gap: LogGap | null = null;
    let cleared = false;                    // did a gap invalidate what the caller holds?
    for (let i = 0; i < maxFetches; i++) {
        let batch: LogBatch | null;
        try {
            batch = (await fetchBatch({ after_seq: cursor, after_boot: boot, limit })) as LogBatch | null;
        } catch {
            break;                          // transient: the cursor stands, the next tick retries
        }
        if (!batch || !Array.isArray(batch.events)) break;
        if (batch.gap) {
            // Told, not guessed. The server has already re-anchored the read; we hand the
            // notice up so a hole in the record is never invisible — a plausible-looking
            // wrong continuation is worse than an admitted gap.
            gap = batch.gap;
            events = [];
            cleared = true;
        }
        events = events.concat(batch.events);
        cursor = batch.next_seq;
        boot = batch.boot_id;
        if (!batch.events.length || batch.next_seq >= batch.last_seq) break;   // at the tail
    }
    return { events, cursor, boot, gap, cleared };
}
