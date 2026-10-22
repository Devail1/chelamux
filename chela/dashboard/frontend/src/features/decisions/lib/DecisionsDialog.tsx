// The Decisions dialog: who owns the inbox (the chip), a search over the held events, and
// the log itself, newest first.
import { useEffect, useState, type KeyboardEvent } from 'react';
import { api } from '../../../shared/api/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { orchestratorSubscribe, type OrchestratorStatus } from '../../../shared/orchestrator/index.ts';
import { agents, type Agent } from '../../../shared/store/agents.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { orchestrator } from '../../../shared/store/orchestrator.ts';
import { Button, Dialog, Select, TextInput } from '../../../shared/ui/index.ts';
import { CLASSES, classOf, type LogEvent } from '../feed.ts';
import { filterDecisionEvents, itemFromDecisionPayload } from '../model.ts';
import { RECOVERABLE_STATES, chipMeta } from './chip.ts';
import { closeDecisions, inbox, inboxOpen, inboxQuery, openDecisionTicket } from './inbox.ts';

// True from the moment a take-over is fired until it settles. A store, not component state:
// the invariant is "one atomic take-over per click", and it must hold across a close and
// reopen of the dialog mid-request, not only across a re-render.
const reregInFlight = createStore(false);

// Only a window with a LIVE Claude session can usefully hold the role: the inbox writes
// prompt text into it (chela/inbox.py::deliver). A bare shell window would just accumulate
// the queue behind a second dead address.
function candidates(rows: Agent[]) {
  return rows
    .filter((a) => a && a.window_id && a.claude_running)
    .sort((a, b) => String(a.name || '').localeCompare(String(b.name || '')) || String(a.window_id).localeCompare(String(b.window_id)));
}

/**
 * The dangling/gone fix: `chela.inbox.register` via /api/orchestrator/subscribe, the same
 * take-over `chela watch` does, one click away from the place that reports the problem.
 * Deliberately NOT a dismiss: suppressing the chip would need persistent state the NEXT
 * dangling address would hide behind too.
 */
function Reregister() {
  const rows = useStore(agents);
  const busy = useStore(reregInFlight);
  const list = candidates(rows);
  const [pick, setPick] = useState('');
  // The cache is filled by the agents poll on some views only, and empty reads exactly like
  // "no live sessions", the wrong thing for this control to claim. So an empty cache is
  // filled once here, best effort (a failed read just keeps the empty state). Not a poller.
  const empty = rows.length === 0;
  useEffect(() => {
    if (!empty) return;
    api('/api/agents').then(
      (v) => agents.set(Array.isArray(v) ? (v as Agent[]) : []),
      () => {},
    );
  }, [empty]);
  if (!list.length) {
    return (
      <div className="decisions-chip-reregister">
        <span className="decisions-chip-rereg-empty">no live Claude session to re-register. Open one, then retry</span>
      </div>
    );
  }
  // The operator's pick survives every re-render while it is still a live candidate; a
  // session that vanished falls back to the first, never carrying a stale wid into the POST.
  const chosen = list.some((a) => a.window_id === pick) ? pick : String(list[0]?.window_id);
  const take = async () => {
    if (reregInFlight.get()) return;
    reregInFlight.set(true);
    try {
      // On success orchestratorSubscribe updates the shared state, which repaints the chip;
      // a refusal leaves it visibly dangling.
      const result = await orchestratorSubscribe(chosen);
      if (!result.ok) console.error('re-register failed', result.error);
    } finally {
      reregInFlight.set(false);
    }
  };
  return (
    <div className="decisions-chip-reregister">
      <Select
        label="Session to re-register as orchestrator"
        value={chosen}
        onChange={setPick}
        options={list.map((a) => ({ value: String(a.window_id), label: `${a.name || a.window_id} (${a.window_id})` }))}
      />
      <Button variant="danger" size="sm" disabled={busy} onClick={() => void take()} title="Register the selected session as the orchestrator: the same take-over 'chela watch' does from a shell">
        <icons.retry aria-hidden size={12} /> Re-register
      </Button>
    </div>
  );
}

function OwnerChip({ s }: { s: OrchestratorStatus }) {
  const meta = chipMeta(s.state);
  return (
    <div className={`decisions-chip decisions-chip-${meta.cls}`}>
      <span className="decisions-chip-glyph" title={meta.word}>
        {meta.glyph} {meta.word}
      </span>
      <span className="decisions-chip-who">{s.wid ? `${s.name || s.wid} (${s.wid})` : 'nobody (this panel is home)'}</span>
      {s.queued ? <span className="decisions-chip-queued">{s.queued} queued</span> : null}
      {s.why && <span className="decisions-chip-why">{s.why}</span>}
      {RECOVERABLE_STATES.has(s.state) && <Reregister />}
    </div>
  );
}

const time = (e: LogEvent) =>
  new Date((e.ts || 0) * 1000).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

// The Feed's row markup and classes (feed-row / feed-cls-* / feed-glyph), so a decision
// reads in one visual language. A row whose payload names a task_id opens its ticket; a bare
// window/inbox-plumbing event (watch_epoch_lost, inbox_undeliverable, ...) has none to open.
function Row({ e }: { e: LogEvent }) {
  const cls = classOf(e.type);
  const meta = CLASSES[cls];
  const clickable = itemFromDecisionPayload(e) != null;
  const open = () => void openDecisionTicket(e);
  return (
    <div
      className={`feed-row feed-cls-${cls}${clickable ? ' feed-row-clickable' : ''}`}
      data-seq={e.seq}
      {...(clickable
        ? {
            role: 'button',
            tabIndex: 0,
            title: 'Open this task',
            onClick: open,
            onKeyDown: (k: KeyboardEvent) => {
              if (k.key === 'Enter' || k.key === ' ') {
                k.preventDefault();
                open();
              }
            },
          }
        : {})}
    >
      <span className="feed-ts">{time(e)}</span>
      <span className="feed-glyph" title={meta.word}>
        {meta.glyph} {meta.word}
      </span>
      <span className="feed-wid">{e.wid || 'chela'}</span>
      <span className="feed-summary">{e.summary || e.type || ''}</span>
    </div>
  );
}

function List() {
  const s = useStore(inbox);
  const query = useStore(inboxQuery);
  const gap = s.gap && (
    <div className="feed-gap">
      <icons.warn aria-hidden size={12} /> {s.gap.reason || 'a gap in the decisions log'}
    </div>
  );
  if (!s.events.length) {
    return (
      <>
        {gap}
        <div className="side-empty">No decisions logged yet</div>
      </>
    );
  }
  // The search filters what is RENDERED, not what is held: clearing it brings the full
  // backlog straight back with no re-fetch.
  const filtered = filterDecisionEvents(s.events, query);
  if (!filtered.length) {
    return (
      <>
        {gap}
        <div className="side-empty">No decisions match "{query}"</div>
      </>
    );
  }
  const rows = filtered.sort((a, b) => (b.seq || 0) - (a.seq || 0));
  return (
    <>
      {gap}
      {/* Say what was actually searched: the dialog holds a bounded buffer, not the whole
          log, so a bare filtered list would imply "the only matches in your history". */}
      {query.trim() && (
        <div className="decisions-scope">
          {filtered.length} of {s.events.length} loaded
        </div>
      )}
      {rows.map((e) => (
        <Row key={e.seq} e={e} />
      ))}
    </>
  );
}

export function DecisionsDialog() {
  const open = useStore(inboxOpen);
  const owner = useStore(orchestrator);
  const query = useStore(inboxQuery);
  return (
    <Dialog open={open} onOpenChange={(o) => !o && closeDecisions()} title="Decisions" className="modal-decisions">
      <div className="decisions-body">
        <OwnerChip s={owner} />
        <TextInput label="Search decisions" placeholder="Search task, branch, PR…" plain autoFocus value={query} onChange={(v) => inboxQuery.set(v)} />
        <div className="decisions-list">
          <List />
        </div>
      </div>
    </Dialog>
  );
}
