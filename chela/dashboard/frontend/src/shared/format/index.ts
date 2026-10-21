// CMX-231 (CMX-212 P10/P11): the dashboard's time and schedule labels, moved from util.js
// (which re-exports shortTime and relativeTime for its legacy importers).

/** `HH:MM Mon D`, or '-' with no time. */
export function shortTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })} ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`;
}

/** How far ahead `iso` is (`5m`, `2h`), 'overdue' once past, '-' with no time. */
export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '-';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const diff = (t - now) / 1000;
  if (diff < 0) return 'overdue';
  if (diff < 60) return `${Math.round(diff)}s`;
  if (diff < 3600) return `${Math.round(diff / 60)}m`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h`;
  return `${Math.round(diff / 86400)}d`;
}

/** How long ago, from seconds: `12s ago`, `5m ago`, `3h ago`, `2d ago`; 'never' with none. */
export function ageStr(seconds: number | null | undefined): string {
  if (seconds == null) return 'never';
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

const pad = (n: number | string) => String(n).padStart(2, '0');

/** An evenly spaced list's step (`[0, 8, 16]` → 8), or null when the gaps differ. */
function step(ns: number[]): number | null {
  const [a, b] = ns;
  if (a === undefined || b === undefined) return null;
  return ns.every((n, i) => i === 0 || n - (ns[i - 1] ?? 0) === b - a) ? b - a : null;
}

/** A schedule as a person says it: `every 5m`, `daily at 09:00`, `3x daily at …`. */
export function humanSchedule(type: string, value: string): string {
  if (type === 'interval') return `every ${value}`;
  if (type === 'once') return 'once';
  if (type !== 'cron') return `${type} ${value}`;
  const parts = value.split(/\s+/);
  if (parts.length !== 5) return `cron ${value}`;
  const [min = '', hr = '', dom, mon, dow] = parts;
  const num = /^\d+$/;
  const list = /^[\d,]+$/;
  if (/^\*\/\d+$/.test(min) && hr === '*') return `every ${min.slice(2)}m`;
  if (num.test(min) && hr === '*') return `every 1h at :${pad(min)}`;
  if (num.test(min) && /^\*\/\d+$/.test(hr)) return `every ${hr.slice(2)}h at :${pad(min)}`;
  if (num.test(min) && num.test(hr)) return `daily at ${pad(hr)}:${pad(min)}`;
  if (num.test(min) && list.test(hr) && dom === '*' && mon === '*' && dow === '*') {
    const hrs = hr.split(',').map(Number).sort((x, y) => x - y);
    const every = step(hrs);
    if (every !== null) return `every ${every}h at :${pad(min)}`;
    return `${hrs.length}x daily at ${hrs.map((h) => `${pad(h)}:${pad(min)}`).join(', ')}`;
  }
  if (list.test(min) && hr === '*') {
    const mins = min.split(',').map(Number).sort((x, y) => x - y);
    const every = step(mins);
    if (every !== null) return `every ${every}m`;
    return `at :${mins.map(pad).join(',:')}`;
  }
  return `cron ${value}`;
}
