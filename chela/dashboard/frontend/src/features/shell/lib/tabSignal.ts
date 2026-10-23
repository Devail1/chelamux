// The tab signal (util.js until CMX-248): when panes block on you (wantsHuman), the title gets a
// count and the favicon an amber badge, so a backgrounded or pinned tab says it needs you: the
// same signal as the sidebar's "Needs you", the wall's amber pane and the push notification.
// Fed by the existing polls (the sidebar's every 30 s and on SSE, the wall's every 4 s); it
// touches the DOM only when the count changes.
import { wantsHuman, type HeadAgent } from '../../panehead/index.ts';

let baseTitle: string | null = null;
let defaultIcon: string | null = null;
let shown = -1; // the last count applied; -1 paints on the first call

/** The page's own title and favicon href, read once before the first badge changes them. */
function captureBase(doc: Document) {
  if (baseTitle !== null) return;
  baseTitle = doc.title;
  defaultIcon = doc.querySelector('link[rel~="icon"]')?.getAttribute('href') ?? 'static/img/favicon.svg';
}

export function updateTabSignal(agents: readonly unknown[] | null | undefined, doc: Document = document): void {
  captureBase(doc);
  const n = (agents ?? []).filter((a) => wantsHuman(a as HeadAgent)).length;
  if (n === shown) return;
  shown = n;
  doc.title = n > 0 ? `(${n}) Needs you · ${baseTitle}` : (baseTitle as string);
  drawFavicon(doc, n);
}

function faviconLink(doc: Document): HTMLLinkElement {
  let l = doc.querySelector<HTMLLinkElement>('link[rel~="icon"]');
  if (!l) {
    l = doc.createElement('link');
    l.rel = 'icon';
    doc.head.appendChild(l);
  }
  return l;
}

/** 0 restores the brand SVG; n > 0 paints an amber disc and the count, the amber read live from
 * the theme's --yellow so it follows a theme switch. */
function drawFavicon(doc: Document, n: number) {
  const link = faviconLink(doc);
  if (n <= 0) {
    link.type = 'image/svg+xml';
    link.href = defaultIcon as string;
    return;
  }
  const amber = getComputedStyle(doc.body).getPropertyValue('--yellow').trim() || '#d29922';
  const size = 32;
  const cv = doc.createElement('canvas');
  cv.width = cv.height = size;
  const c = cv.getContext('2d');
  if (c) {
    c.beginPath();
    c.arc(size / 2, size / 2, size / 2, 0, 2 * Math.PI);
    c.fillStyle = amber;
    c.fill();
    const label = n > 9 ? '9+' : String(n);
    c.fillStyle = '#1b1f24'; // dark ink, the favicon's light-scheme color
    c.font = `bold ${label.length > 1 ? 17 : 22}px system-ui, -apple-system, sans-serif`;
    c.textAlign = 'center';
    c.textBaseline = 'middle';
    c.fillText(label, size / 2, size / 2 + 1);
  }
  link.type = 'image/png';
  link.href = cv.toDataURL('image/png');
}
