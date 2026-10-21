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
  const root = createRoot(el);
  root.render(<Component {...props} />);
  return root;
}
