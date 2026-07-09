// Pure letterbox fit — extracted from presence.js so it is unit-testable and
// STRUCTURALLY immune to the §5.1 shrink spiral.
//
// The spiral came from measuring a container that the fit itself changes: once
// body.chela-shared became a flex box it shrink-wrapped to its content, so each
// pass read a smaller box and drove the font smaller again (a feedback loop).
//
// The cure is to depend only on INVARIANTS:
//   - outerW/outerH  → the fixed viewport (window.innerWidth/innerHeight), never
//     the element being centered;
//   - cellWPerPx/cellHPerPx → the font's cell size per 1px of fontSize, a font
//     constant (cellPx = ratio * fontSize), measured from the current render but
//     independent of which font size that was.
//
// Because none of the inputs depend on the fontSize we are about to set, the
// result is idempotent: applying it and recomputing yields the same value, so
// fit(fit(x)) === fit(x) and no spiral is possible. Integer px (floor) means a
// thin letterbox margin — deliberate, over an exact-fill blur.
export function computeFit(outerW, outerH, cols, rows, cellWPerPx, cellHPerPx) {
  if (!(outerW > 0 && outerH > 0 && cols > 0 && rows > 0 && cellWPerPx > 0 && cellHPerPx > 0)) {
    return null;
  }
  const byWidth = outerW / (cols * cellWPerPx);
  const byHeight = outerH / (rows * cellHPerPx);
  return Math.max(6, Math.floor(Math.min(byWidth, byHeight)));
}
