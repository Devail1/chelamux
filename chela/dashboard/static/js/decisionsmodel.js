// ---------------------------------------------------------------------------
// DECISIONS BADGE MODEL — pure unread-count/urgency math for the header
// #btn-decisions button's #decisions-unread badge. No DOM, no fetch, no
// localStorage — decisions.js owns persistence (the `chela.decisions.lastSeen`
// key) and wires this into the DOM; this file only ever answers "given the
// held events and a last-seen seq, what does the badge say", so the arithmetic
// is directly unit-testable (tests/decisions_badge.test.mjs) without a jsdom
// fixture — the same split feedmodel.js draws for the Feed's lane grouping.
//
// Unread = held decision events with a seq beyond what the popover has already
// shown you (`seq > lastSeenSeq`, strictly — `>=` would re-badge the event you
// just saw as still unread).
// ---------------------------------------------------------------------------

// Which decision kinds earn an urgency colour on the badge, not just a neutral
// count. 'bad' (vermillion) = something broke, was lost, or is stuck — needs a
// human now. 'warn' (orange) = a checkpoint that needs a human's judgement but
// nothing is broken. A type absent from this map (finished, completed_gone,
// inbox_self_healed, …) is informational — neutral count colour only. Colour
// is never the sole signal here either: the number itself is what carries the
// "you have unread decisions" fact; urgency is decoration on top of it.
export const DECISION_URGENCY = {
    run_failed: 'bad',
    died: 'bad',
    gone_unknown: 'bad',
    inbox_undeliverable: 'bad',
    watch_epoch_lost: 'bad',
    run_review: 'warn',
    run_needs_human: 'warn',
    run_changes_requested: 'warn',
    blocked: 'warn',
};

export function unseenEvents(events, lastSeenSeq) {
    const since = lastSeenSeq || 0;
    return (events || []).filter(e => ((e && e.seq) || 0) > since);
}

export function unreadCount(events, lastSeenSeq) {
    return unseenEvents(events, lastSeenSeq).length;
}

// Display cap — a fleet backlog is not a number worth reading past two digits.
export function formatUnreadCount(count) {
    if (!count) return '';
    return count > 99 ? '99+' : String(count);
}

export function maxSeq(events) {
    return (events || []).reduce((m, e) => Math.max(m, (e && e.seq) || 0), 0);
}

// First-ever load (no stored lastSeen): seed to the CURRENT max held seq,
// never to 0 — 0 would badge the entire historical backlog as unread the
// moment the popover first ships to a browser that has never opened it.
export function seedLastSeen(events) {
    return maxSeq(events);
}

export function unreadUrgency(events, lastSeenSeq) {
    const unseen = unseenEvents(events, lastSeenSeq);
    if (unseen.some(e => DECISION_URGENCY[e.type] === 'bad')) return 'bad';
    if (unseen.some(e => DECISION_URGENCY[e.type] === 'warn')) return 'warn';
    return 'neutral';
}
