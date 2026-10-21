// CMX-178: an island is a React root rendered into an element the legacy page already has.
// Legacy keeps owning the rest of the page; each island takes one region (docs/FRONTEND.md).
import type { ComponentType } from 'react';
import { createRoot, type Root } from 'react-dom/client';

/** Render `Component` with `props` into the element `#id`. Returns its root, or null when the
 * page has no such element (a template without the slot): nothing is mounted then. */
export function mountIsland<P extends object>(
  id: string,
  Component: ComponentType<P>,
  props: P,
  doc: Document = document,
): Root | null {
  const el = doc.getElementById(id);
  if (!el) return null;
  const root = createIslandRoot(el);
  root.render(<Component {...props} />);
  return root;
}

/** A React root on `el`, marked `data-island` (CMX-301). Legacy's touch tap-tip (nav.js) bails
 * on that mark: it cannot see React's onClick, and its preventDefault on a titled descendant
 * ate the tap, so a phone tap on a sidebar row showed the row's tooltip and never opened it.
 * Every root goes through here; eslint refuses a bare createRoot anywhere else. */
export function createIslandRoot(el: Element): Root {
  el.setAttribute('data-island', '');
  return createRoot(el);
}
