// What the schedules tables and the add-schedule form show (pure). CMX-231.

export type Schedule = {
  id: number;
  agent_name: string;
  schedule_type: string;
  schedule_value: string;
  prompt: string;
  last_run?: string | null;
  next_run?: string | null;
  enabled: boolean;
};

export type CronJob = { schedule: string; command: string; project?: string | null; tz?: string | null; next_run?: string | null };

// The value field's label and placeholder for each schedule type, so the user knows what
// format it expects (chela/models.py schedule_value).
const HINTS: Record<string, { label: string; placeholder: string }> = {
  interval: { label: 'Interval (e.g. 30s, 5m, 1h)', placeholder: '5m' },
  cron: { label: 'Cron expression (5 fields: min hr dom mon dow)', placeholder: '0 */8 * * *' },
  once: { label: 'Run at (ISO 8601, e.g. 2026-01-15T09:00)', placeholder: 'YYYY-MM-DDTHH:MM' },
};

export const SCHEDULE_TYPES = [
  { value: 'interval', label: 'Interval' },
  { value: 'cron', label: 'Cron' },
  { value: 'once', label: 'Once' },
];

export function scheduleHint(type: string): { label: string; placeholder: string } {
  return HINTS[type] ?? { label: 'Interval (e.g. 30s, 5m, 1h)', placeholder: '5m' };
}

/** `s` cut to `n` characters plus `tail` when longer. */
export function clip(s: string, n: number, tail = '…'): string {
  return s.length > n ? s.slice(0, n) + tail : s;
}
