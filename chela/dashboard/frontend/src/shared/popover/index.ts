// CMX-218 (CMX-212 P04): the legacy popovers' placement (CMX-398), moved verbatim out of
// nav.js. Legacy menus and tap tooltips use it until their slices port onto ui/'s <Popover>,
// whose placement is Radix's. popoverPosition() is the pure math; placePopover() measures,
// applies it, and re-places every open popover on window resize.

/** The margin every anchored popover keeps from each viewport edge. */
export const POPOVER_MARGIN = 8;

export type PopoverOpts = { gap?: number; align?: 'right' | 'center' };

type Rect = { top: number; bottom: number; left: number; right: number; width: number };

/**
 * Where a position:fixed popover of size `w`×`h` goes next to `anchor`: BELOW it when it
 * fits, otherwise ABOVE it; neither side has room ⇒ the roomier side, pushed back inside.
 * Right-aligned to the anchor (`align: 'center'` centres it), clamped inside the viewport
 * with POPOVER_MARGIN on every side. A popover taller than the viewport allows gets
 * `maxHeight` (and scrolls) instead of overflowing; `maxHeight` is null when it fits.
 */
export function popoverPosition(
  { vw, vh, anchor: r, w, h }: { vw: number; vh: number; anchor: Rect; w: number; h: number },
  { gap = 6, align = 'right' }: PopoverOpts = {},
): { top: number; left: number; maxHeight: number | null } {
  const M = POPOVER_MARGIN;
  const room = Math.max(0, vh - 2 * M);
  const maxHeight = h > room ? room : null;
  if (maxHeight !== null) h = room;
  let top;
  if (r.bottom + gap + h <= vh - M) top = r.bottom + gap; // below
  else if (r.top - gap - h >= M) top = r.top - gap - h; // above
  else top = vh - r.bottom >= r.top ? r.bottom + gap : r.top - gap - h;
  top = Math.max(M, Math.min(top, vh - M - h));
  let left = align === 'center' ? r.left + r.width / 2 - w / 2 : r.right - w;
  left = Math.max(M, Math.min(left, vw - M - w));
  return { top, left, maxHeight };
}

// Open popovers placed by placePopover, re-placed on window resize. Keyed by the popover;
// an entry drops out once its popover is hidden or detached.
const placed = new Map<HTMLElement, { anchor: HTMLElement; opts: PopoverOpts }>();

function isOpen(m: HTMLElement) {
  return m.isConnected && !m.hidden && m.style.display !== 'none';
}

/**
 * Anchor `m` to `anchor` (popoverPosition) and keep it there across window resizes. The
 * popover must be displayed first: a display:none element has no size to measure.
 */
export function placePopover(m: HTMLElement | null, anchor: HTMLElement | null, opts: PopoverOpts = {}) {
  if (!m || !anchor) return;
  // Measure the popover's own height, not a max-height a previous (smaller) viewport left.
  m.style.maxHeight = '';
  m.style.overflowY = '';
  const { top, left, maxHeight } = popoverPosition(
    { vw: window.innerWidth, vh: window.innerHeight, anchor: anchor.getBoundingClientRect(), w: m.offsetWidth, h: m.offsetHeight },
    opts,
  );
  if (maxHeight !== null) {
    m.style.maxHeight = maxHeight + 'px';
    m.style.overflowY = 'auto';
  }
  m.style.top = top + 'px';
  m.style.left = left + 'px';
  placed.set(m, { anchor, opts: { gap: opts.gap ?? 6, align: opts.align ?? 'right' } });
}

/** The anchor and options `m` was last placed with, so a menu page swap re-places it. */
export function placedPopover(m: HTMLElement) {
  return placed.get(m);
}

if (typeof window !== 'undefined') {
  window.addEventListener('resize', () => {
    for (const [m, { anchor, opts }] of placed) {
      if (isOpen(m) && anchor.isConnected) placePopover(m, anchor, opts);
      else placed.delete(m);
    }
  });
}

let tapTipsOn = false;

/**
 * Touch tooltips (moved from nav.js by CMX-219). A native `title` only shows on hover, so on a
 * phone the titled readouts (the CPU/RAM/Disk strip, the usage readouts) had no tooltip at all.
 * Tapping a titled element pops a .tap-tip bubble; tapping elsewhere, scrolling or 4 s
 * dismisses it. Desktop hover keeps the native title. Installed once per page.
 */
export function installTapTips(win: Window = window) {
  if (tapTipsOn) return;
  tapTipsOn = true;
  const doc = win.document;
  let tip: HTMLElement | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const hide = () => {
    if (timer) clearTimeout(timer);
    timer = null;
    tip?.remove();
    tip = null;
  };
  // Touch only. NEVER an interactive control: preventDefault() on touchend suppresses the
  // follow-up click, so hijacking a tap on a button, link or row would kill its action.
  doc.addEventListener('touchend', (e) => {
    const target = e.target as Element | null;
    if (typeof target?.closest !== 'function' || target.closest('button, a, input, select, textarea, label, [onclick], [role="button"]')) return hide();
    // CMX-301: a React row's onClick is invisible to the selector above, so a tap on its
    // titled name popped "window: …" and the session never opened. What is clickable is
    // drawn with the pointer cursor (inherited by everything inside it): that is the test.
    if (win.getComputedStyle(target).cursor === 'pointer') return hide();
    const el = target.closest<HTMLElement>('[title], [data-tip]');
    const text = el?.getAttribute('data-tip') || el?.getAttribute('title');
    if (!el || !text) return hide();
    e.preventDefault();
    hide();
    tip = doc.createElement('div');
    tip.className = 'tap-tip';
    tip.textContent = text;
    doc.body.appendChild(tip);
    // centred under the target, or above it at the bottom of the screen (the sidebar foot)
    placePopover(tip, el, { gap: 6, align: 'center' });
    timer = setTimeout(hide, 4000);
  }, { passive: false });
  win.addEventListener('scroll', hide, true);
}
