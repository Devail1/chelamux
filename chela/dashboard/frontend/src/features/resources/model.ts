// HOST RESOURCES MODEL: the CPU/RAM/Disk strip's display math (CMX-172), pure. Moved from
// static/js/resourcesmodel.js by CMX-219 (CMX-212 P06).
//
// pct() mirrors chela/dashboard/resources.py::pct (same zero-guard, same clamp), so a value
// computed either side of the wire reads identically.

export function pct(used: number, total: number): number {
  if (!total) return 0;
  return Math.max(0, Math.min(100, Math.round((used / total) * 1000) / 10));
}

/** The hue a % is tinted with. The % NUMBER is always the signal (Liav is red-weak); this
 * only decides the secondary colour. */
export function level(p: number): 'ok' | 'warn' | 'bad' {
  if (p >= 90) return 'bad';
  if (p >= 75) return 'warn';
  return 'ok';
}

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];

/** `512 B`, `1.5 KB`, `42 GB`; '-' when unknown. */
export function humanBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '-';
  if (n < 1024) return `${Math.round(n)} B`;
  let value = n;
  let i = 0;
  while (value >= 1024 && i < UNITS.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${UNITS[i]}`;
}
