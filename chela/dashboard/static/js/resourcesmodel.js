// ---------------------------------------------------------------------------
// HOST RESOURCES MODEL — pure CPU/RAM/Disk display math for the header
// #resources-strip (CMX-172). No DOM, no fetch: resources.js owns the
// /api/resources poll and the render; this file only ever answers "given a
// percentage or a byte count, what does the strip say", so it is directly
// unit-testable (tests/resources_model.test.mjs), same split decisionsmodel.js
// draws for the unread badge.
//
// pct() mirrors chela/dashboard/resources.py::pct byte-for-byte (same
// zero-guard, same clamp) so a value computed either side of the wire reads
// identically.
// ---------------------------------------------------------------------------

export function pct(used, total) {
    if (!total) return 0;
    return Math.max(0, Math.min(100, Math.round((used / total) * 1000) / 10));
}

// Thresholds the header strip tints at — the % NUMBER is always the signal
// (Liav is red-weak), this classification only decides the secondary hue.
export function level(p) {
    if (p >= 90) return 'bad';
    if (p >= 75) return 'warn';
    return 'ok';
}

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];

export function humanBytes(n) {
    if (n == null || !Number.isFinite(n)) return '—';
    if (n < 1024) return `${Math.round(n)} B`;
    let value = n;
    let i = 0;
    while (value >= 1024 && i < UNITS.length - 1) {
        value /= 1024;
        i++;
    }
    return `${value.toFixed(value >= 10 ? 0 : 1)} ${UNITS[i]}`;
}
