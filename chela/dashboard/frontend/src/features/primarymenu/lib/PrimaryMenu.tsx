// The primary menu (CMX-212 P20, ported from nav.js's #primary-menu and header.js by CMX-219):
// the sidebar foot's gear (CMX-393). Jump to..., New..., Share current session, Notifications
// and Settings, then the ambient readouts: 5h and 7d usage, Schedules, Next run, Updated.
// New... opens the launch menu at the gear. The safety kill switch is deliberately NOT here: it
// stays visible whenever a share is live.
import { useRef } from 'react';
import { BOOT, api } from '../../../shared/api/index.ts';
import { relativeTime } from '../../../shared/format/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { registerLane } from '../../../shared/scheduler/index.ts';
import { context, loadContext } from '../../../shared/store/context.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { IconButton, Menu, type MenuItem } from '../../../shared/ui/index.ts';
import { openLaunchMenu } from '../../launcher/index.ts';
import { openPalette } from '../../palette/index.ts';
import { toggleSettings } from '../../settings/index.ts';
import { agentsCount, rlReading, soonest, type RlReading, type Summary } from '../model.ts';

/** The last /api/summary and when it landed; null until the first. */
export const summary = createStore<{ data: Summary; at: Date } | null>(null);

export async function refreshSummary(): Promise<void> {
  const data = (await api('/api/summary')) as Summary | null;
  if (data) summary.set({ data, at: new Date() });
}

type Legacy = { shareCurrentAgent?: () => void };
// terminals.js owns sharing the current pane until its slice ports it: looked up at call time.
const legacy = (): Legacy => (window as unknown as { chela?: Legacy }).chela ?? {};

function Usage({ id, label, title, r }: { id: string; label: string; title: string; r: RlReading | null }) {
  if (!r) return null;
  return (
    <div className="ov-stat" id={id} title={r.tip || title} style={r.stale ? { opacity: 0.5 } : undefined}>
      <span className="ov-lbl">{label}</span><span className={`value ${r.tone}`.trim()}>{r.pct}%</span>
    </div>
  );
}

function Readouts() {
  const rows = useStore(context);
  const s = useStore(summary);
  const next = soonest(s?.data.next_runs);
  return (
    <>
      <div className="popover-sep" />
      <Usage id="hdr-ratelimit-pill" label="5h usage" title="5-hour usage" r={rlReading(rows, 'rate_limit_pct', 'rate_limit_resets_at')} />
      <Usage id="hdr-weekly-rl-pill" label="7d usage" title="7-day usage" r={rlReading(rows, 'weekly_rl_pct', 'weekly_rl_resets_at')} />
      <div className="ov-stat" title="Enabled / total scheduled tasks">
        <span className="ov-lbl">Schedules</span><span className="value" id="hdr-schedules">{s ? `${s.data.schedules_active}/${s.data.schedules_total}` : '-'}</span>
      </div>
      <div className="ov-stat" title="Soonest scheduled run">
        <span className="ov-lbl">Next run</span><span className="value ts" id="hdr-next">{!s ? '-' : next ? `${next[0]} in ${relativeTime(next[1])}` : 'none'}</span>
      </div>
      <div className="ov-stat">
        <span className="ov-lbl">Updated</span>
        <span className="value ts" id="hdr-updated">{s ? s.at.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : '-'}</span>
      </div>
    </>
  );
}

/** The gear (#btn-primary-menu) and its menu. */
export function PrimaryMenu() {
  const gear = useRef<HTMLButtonElement>(null);
  const items: MenuItem[] = [
    { label: 'Jump to…', icon: icons.search, value: '⌘K', onSelect: () => openPalette() },
    { label: 'New…', icon: icons.add, onSelect: () => {
        // after the menu has closed and handed focus back to the gear
        setTimeout(() => gear.current && openLaunchMenu(gear.current), 0);
      } },
    'separator',
    ...(BOOT.terminalsEnabled ? [{ label: 'Share current session', icon: icons.share, onSelect: () => legacy().shareCurrentAgent?.() }] : []),
    { label: 'Notifications', icon: icons.notifications, onSelect: () => toggleSettings('notify') },
    { label: 'Settings', icon: icons.settings, onSelect: () => toggleSettings() },
  ];
  return (
    <Menu items={items} footer={<Readouts />}
      trigger={<IconButton ref={gear} className="icon-btn" id="btn-primary-menu" label="Settings & menu"><icons.settings aria-hidden /></IconButton>} />
  );
}

/** The Sessions header's agent count (#hdr-agents), from the same summary. Its classes are
 * header.js's exactly: the `.side-count` chip until the first summary, then `value <tone>`
 * (the header's row height and the sidebar rows under it are measured against that). */
export function SessionsCount() {
  const s = useStore(summary);
  const c = s ? agentsCount(s.data) : null;
  return <span className={c ? `value ${c.tone}`.trim() : 'side-count'} id="hdr-agents">{c ? c.text : '-/-'}</span>;
}

registerLane('summary', () => refreshSummary());
// The agent bars and the usage readouts, on every view, once the tick's reads settle.
registerLane('context', (t) => loadContext(t.context), 'settled');
