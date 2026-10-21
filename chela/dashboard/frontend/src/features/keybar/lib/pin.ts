// Pinning the phone keybar above the soft keyboard, and fitting the single pane to it
// (CMX-102, CMX-154). Reads the live page globals at call time, as the legacy code it
// replaces did (tests swap the page's window under it).
import { keyboardOccluded, paneFitHeight, pinTop } from '../model.ts';

const isPhone = () => window.matchMedia('(max-width: 768px)').matches;
const SINGLE_PANE = '#term-stage.term-single .term-pane';

/** CMX-154: make the phone pane's terminal re-fit to the box it now has. iPhone Safari keeps
 * xterm's taller row count after its iframe shrank, so we call ttyd's own `term.fit()` (a
 * no-op when rows/cols already match). Desktop keeps ttyd's own resize handling. */
export function refitPaneTerm() {
  if (!isPhone()) return;
  const ifr = document.querySelector<HTMLIFrameElement>(`${SINGLE_PANE} iframe.term-frame`);
  let term: { fit?: unknown } | undefined;
  try {
    term = (ifr?.contentWindow as (Window & { term?: { fit?: unknown } }) | null | undefined)?.term;
  } catch {
    return; // an unreadable frame
  }
  if (term && typeof term.fit === 'function') (term.fit as () => void)();
}

function fitPane(vv: VisualViewport, bar: HTMLElement, occluded: number) {
  const root = document.documentElement;
  const pane = document.querySelector(SINGLE_PANE);
  const fit = pane
    ? paneFitHeight({
        mobile: isPhone(),
        occluded,
        vvTop: vv.offsetTop,
        vvHeight: vv.height,
        barHeight: bar.offsetHeight,
        paneTop: pane.getBoundingClientRect().top,
      })
    : null;
  if (fit === null) {
    root.classList.remove('kb-fit');
    root.style.removeProperty('--kb-pane-fit-h');
  } else {
    root.style.setProperty('--kb-pane-fit-h', `${fit}px`);
    root.classList.add('kb-fit');
  }
  refitPaneTerm();
}

/** Pin #term-keybar to the visual viewport's bottom and publish --kb-occluded (what the soft
 * keyboard covers: `dvh` does not track it, VisualViewport is the only thing that reports
 * it). Best-effort: without VisualViewport the CSS bottom:0 keeps the bar in place. */
export function pinKeybar() {
  const vv = window.visualViewport;
  if (!vv) return;
  const occluded = keyboardOccluded({ mobile: isPhone(), innerHeight: window.innerHeight, vvTop: vv.offsetTop, vvHeight: vv.height });
  document.documentElement.style.setProperty('--kb-occluded', `${occluded}px`);
  const bar = document.getElementById('term-keybar');
  if (!bar) return;
  bar.style.bottom = 'auto';
  bar.style.top = `${pinTop({ vvTop: vv.offsetTop, vvHeight: vv.height, barHeight: bar.offsetHeight })}px`;
  fitPane(vv, bar, occluded);
}

/** Re-pin on every geometry change: the viewport (keyboard, iOS pan), the window
 * (rotation), the phone scroller (it moves the pane's top) and the bar's own height. */
export function bindKeybarPin(bar: HTMLElement): () => void {
  const vv = window.visualViewport;
  const canvas = document.querySelector('.canvas');
  vv?.addEventListener('resize', pinKeybar);
  vv?.addEventListener('scroll', pinKeybar);
  window.addEventListener('resize', pinKeybar);
  canvas?.addEventListener('scroll', pinKeybar, { passive: true });
  const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => pinKeybar()) : null;
  ro?.observe(bar);
  pinKeybar();
  return () => {
    vv?.removeEventListener('resize', pinKeybar);
    vv?.removeEventListener('scroll', pinKeybar);
    window.removeEventListener('resize', pinKeybar);
    canvas?.removeEventListener('scroll', pinKeybar);
    ro?.disconnect();
  };
}
