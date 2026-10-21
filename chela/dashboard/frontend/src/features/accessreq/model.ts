// Access requests from sandboxed guests (CMX-7), the pure half. Moved from
// static/js/accessreq.js by CMX-219 (CMX-212 P05).

/** One record of /api/share-requests `requests` (chela/share_requests.py). */
export type AccessRequest = {
  id: string;
  status: string;
  kind?: string | null;
  /** What the guest asked for: `rw` on a mount asks for write. */
  access?: string | null;
  description?: string | null;
  reason?: string | null;
  window?: string | null;
  wid?: string | null;
  sid?: string | null;
  /** Why the server will not approve it (a secrets dir, $HOME, /mnt/*...). */
  refusal?: string | null;
  seconds_left?: number | null;
  rw?: boolean;
};

/** The durations an approval can last, in minutes; the server's default joins them. */
export const DURATIONS = [15, 60, 240, 1440];

export function durLabel(n: number): string {
  if (n % 1440 === 0) return `${n / 1440}${n === 1440 ? ' day' : ' days'}`;
  return n % 60 === 0 ? `${n / 60} h` : `${n} min`;
}

/** The duration choices, sorted, with the server's default among them. */
export function durationChoices(dflt: number): number[] {
  return [...new Set([...DURATIONS, dflt])].sort((a, b) => a - b);
}

export function who(r: AccessRequest): string {
  return r.window || r.wid || `sandbox ${r.sid}`;
}

/** What is left of an approval: `5 min left`, `1h 30m left`; '' when unknown. */
export function timeLeft(s: number | null | undefined): string {
  if (s == null) return '';
  const m = Math.ceil(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m left` : `${m} min left`;
}

export function pendingCount(rows: AccessRequest[] | null | undefined): number {
  return (rows || []).filter((r) => r.status === 'pending').length;
}
