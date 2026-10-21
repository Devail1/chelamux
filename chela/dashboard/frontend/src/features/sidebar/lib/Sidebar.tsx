// CMX-276: the sidebar's session list (#sidebar-agents): Needs you, the groups in their
// order (Dispatched last), the Archived group, and their menus (CMX-277). What an action
// DOES is controller.ts's (through `actions`, CMX-278).
import { useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import {
  ARCHIVED_SECTION_KEY, DISPATCHED_KEY, type Group, type Item, type SidebarState, sidebarModel,
} from '../model.ts';
import type { SidebarActions } from './actions.ts';
import { GroupMenu, RowMenu, useMenuOpen } from './menus.tsx';
import { type Meta, type MetaKind, ago, rowView } from './rowview.ts';

export type { SidebarActions } from './actions.ts';

const META_ICON: Partial<Record<MetaKind, (typeof icons)[keyof typeof icons]>> = {
  orchestrator: icons.orchestrator,
  pr: icons.pullRequest,
  judging: icons.judging,
};

function MetaChip({ m }: { m: Meta }) {
  const Icon = META_ICON[m.kind];
  return (
    <span className={m.cls} data-meta={m.kind} role="img" aria-label={m.title} title={m.title}>
      {Icon && <Icon size={12} aria-hidden="true" />}
      {m.text && <span className="ar-meta-text">{m.text}</span>}
    </span>
  );
}

// An archived row's way back (CMX-75): on a closed session it RESUMES it. Disabled at once,
// so a slow resume is not double-clicked into two windows.
function Unarchive({ itemKey, resume, actions }: { itemKey: string; resume: boolean; actions: SidebarActions }) {
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);   // a second click before the re-render must not resume twice
  const what = resume ? 'Unarchive: resume this session in a new window' : 'Unarchive';
  const click = (e: MouseEvent) => {
    e.stopPropagation();
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    void Promise.resolve(actions.unarchiveRow(null, itemKey)).finally(() => {
      inFlight.current = false;
      setBusy(false);
    });
  };
  return (
    <button className="row-unarchive" data-item={itemKey} title={what} aria-label={what} disabled={busy} onClick={click}>
      {busy ? (resume ? 'Resuming…' : 'Unarchiving…') : 'Unarchive'}
    </button>
  );
}

// CMX-278: a hover (style.css) or a long press shows the whole row: the title wraps instead
// of truncating. The long press's own click does not also open the session.
export const LONG_PRESS_MS = 500;
function useLongPress() {
  const [full, setFull] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const fired = useRef(false);
  const cancel = () => clearTimeout(timer.current);
  return {
    full,
    /** True once for the click that ends a long press: swallow it. */
    consumed: () => {
      const f = fired.current;
      fired.current = false;
      return f;
    },
    handlers: {
      onTouchStart: () => {
        cancel();
        fired.current = false;
        setFull(false);
        timer.current = setTimeout(() => {
          fired.current = true;
          setFull(true);
        }, LONG_PRESS_MS);
      },
      onTouchMove: cancel,
      onTouchEnd: cancel,
    },
  };
}

interface RowProps {
  it: Item;
  s: SidebarState;
  actions: SidebarActions;
  archived?: boolean;
  altNum?: number;
  children?: ReactNode;
}

function Row({ it, s, actions, archived = false, altNum, children }: RowProps) {
  const menu = useMenuOpen();
  const press = useLongPress();
  const v = rowView(it, s);
  if (!v) return null;
  const name = v.agent.name ?? '';
  const cls = ['agent-row', 'rich', name === s.active && 'active', v.onWall && 'on-wall',
    v.runId && 'run-row', archived && 'archived', press.full && 'show-full'].filter(Boolean).join(' ');
  return (
    <div className={cls} data-agent={name} data-run={v.runId ?? undefined} data-item={it.key} title={v.title}
      onClick={() => { if (!press.consumed()) actions.selectAgent(name); }} onContextMenu={menu.onContext} {...press.handlers}>
      <span className={`term-status-dot ${v.dotCls}`} role="img" aria-label={v.dotLabel} title={v.dotLabel} />
      <span className="agent-row-name" data-key={v.windowKey || undefined}
        title={v.windowKey ? `window: ${v.windowKey}` : undefined}>{v.label}</span>
      <span className="ar-meta">
        {v.meta.map(m => <MetaChip key={m.kind} m={m} />)}
        {/* CMX-417: the `@N` that chela peek and the inbox use, shown on hover */}
        {v.agent.window_id && <span className="ar-more"><span className="ar-wid">{v.agent.window_id}</span></span>}
      </span>
      <RowMenu it={it} s={s} actions={actions} menu={menu} />
      {children}
      {altNum ? <span className="ar-altnum" aria-hidden="true">{altNum}</span> : null}
    </div>
  );
}

// A CLOSED session's row (CMX-75): no window to open, only its resume record.
function SessionRow({ it, s, actions }: { it: Item; s: SidebarState; actions: SidebarActions }) {
  const r = it.record ?? { key: it.key };
  const label = r.name || r.session_id || it.key;
  const folder = String(r.cwd || '').replace(/\/+$/, '').split('/').pop() || r.cwd || '';
  const when = r.archived_at ? `archived ${ago(Math.max(0, s.now / 1000 - r.archived_at))} ago` : '';
  const title = [`${label}: archived (closed)`, r.cwd, when].filter(Boolean).join('\n');
  return (
    <div className="agent-row rich archived session-row" data-item={it.key} title={title}>
      <span className="term-status-dot idle" role="img" aria-label="Session: archived" title="Session: archived" />
      <span className="agent-row-name">{label}</span>
      {folder && <span className="ar-meta"><span className="ar-folder">{folder}</span></span>}
      <Unarchive itemKey={it.key} resume actions={actions} />
    </div>
  );
}

interface GroupProps {
  g: Group;
  index: number;
  count: number;
  s: SidebarState;
  actions: SidebarActions;
  alt: Map<string, number>;
}

function GroupView({ g, index, count, s, actions, alt }: GroupProps) {
  const menu = useMenuOpen();
  const rows = g.items.map(it => <Row key={it.key} it={it} s={s} actions={actions} altNum={alt.get(it.key)} />);
  if (g.flat) {
    // Group by ▸ None: one flat list, no header.
    return <div className="side-group flat" data-g={g.key}><div className="group-rows">{rows}</div></div>;
  }
  const collapsed = s.collapsed.includes(g.key);
  const n = g.items.length;
  const stop = (f: (e: MouseEvent) => void) => (e: MouseEvent) => {
    e.stopPropagation();
    f(e);
  };
  return (
    <div className={`side-group${collapsed ? ' collapsed' : ''}${n ? '' : ' empty'}`} data-g={g.key}>
      <div className="group-head" data-g={g.key} aria-expanded={!collapsed}
        onClick={() => actions.toggleGroup(g.key)} onContextMenu={menu.onContext}>
        <span className="group-name" title={g.cwd || g.label}>{g.label}</span>
        {g.key === DISPATCHED_KEY && <span className="group-count" title={`${n} run${n === 1 ? '' : 's'}`}>{n}</span>}
        <span className="group-caret"><icons.expand size={12} aria-hidden="true" /></span>
        <span className="group-actions">
          <GroupMenu g={g} index={index} count={count} s={s} actions={actions} menu={menu} />
          {g.cwd && (
            <button className="group-add" data-g={g.key} title={`New session in ${g.label}`}
              aria-label={`New session in ${g.label}`} onClick={stop(() => actions.groupNewSession(g.key))}>
              <icons.add size={14} aria-hidden="true" />
            </button>
          )}
        </span>
      </div>
      <div className="group-rows">{rows}</div>
    </div>
  );
}

// CMX-85: Alt+1..9 numbers the first nine rows a user can see, top to bottom: Needs you,
// then each group that is not collapsed. The Archived section is not numbered.
function altNumbers(needsYou: Item[], groups: Group[], s: SidebarState): Map<string, number> {
  const out = new Map<string, number>();
  if (!s.altHeld) return out;
  const visible = [...needsYou, ...groups.filter(g => g.flat || !s.collapsed.includes(g.key)).flatMap(g => g.items)];
  for (const it of visible.filter(it => rowView(it, s)).slice(0, 9)) out.set(it.key, out.size + 1);
  return out;
}

export function Sidebar({ state: s, actions }: { state: SidebarState | null; actions: SidebarActions }) {
  if (!s || !s.agents.length) return <div className="side-empty">No agents</div>;
  const model = sidebarModel(s);
  const alt = altNumbers(model.needsYou, model.groups, s);
  const showArchived = s.view.status !== 'active';
  const archivedOpen = s.view.status === 'archived' || s.collapsed.includes(`${ARCHIVED_SECTION_KEY}:open`);
  const nArch = model.archived.length;
  const drawsArchive = showArchived && nArch > 0;
  return (
    <>
      {model.needsYou.length > 0 && (
        <div className="side-triage side-needs-you">
          <div className="triage-head">Needs you <span className="triage-count">{model.needsYou.length}</span></div>
          {model.needsYou.map(it => <Row key={it.key} it={it} s={s} actions={actions} altNum={alt.get(it.key)} />)}
        </div>
      )}
      {model.groups.map((g, i) => (
        <GroupView key={g.key} g={g} index={i} count={model.groups.length} s={s} actions={actions} alt={alt} />
      ))}
      {drawsArchive && (
        <div className={`side-group side-archived${archivedOpen ? '' : ' collapsed'}`} data-g={ARCHIVED_SECTION_KEY}>
          <div className="group-head" data-g={ARCHIVED_SECTION_KEY} aria-expanded={archivedOpen}
            onClick={() => actions.toggleArchivedSection()}>
            <span className="group-name">Archived</span>
            <span className="group-count archived-count" title={`${nArch} archived`}>{nArch}</span>
            <span className="group-caret"><icons.expand size={12} aria-hidden="true" /></span>
          </div>
          <div className="group-rows">
            {model.archived.map(it => it.kind === 'session'
              ? <SessionRow key={it.key} it={it} s={s} actions={actions} />
              : (
                <Row key={it.key} it={it} s={s} actions={actions} archived>
                  <Unarchive itemKey={it.key} resume={false} actions={actions} />
                </Row>
              ))}
          </div>
        </div>
      )}
      {!model.needsYou.length && !model.groups.length && !drawsArchive
        && <div className="side-empty">No sessions match this view</div>}
    </>
  );
}
