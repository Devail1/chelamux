// CMX-248: the page's one React root, in index.html's #root.
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { App } from './App.tsx';
import { SLOTS } from './slots.tsx';

/** Render <App> into `#root`, synchronously: the features that find their elements by id (the
 * Wall's stage, the sidebar's rows) start right after. Null when the page has no #root. */
export function mountApp(doc: Document = document): Root | null {
  const el = doc.getElementById('root');
  if (!el) return null;
  const root = createRoot(el);
  flushSync(() => root.render(<App />));
  return root;
}

/** A page of its own without #root (a test fixture of a few slots): draw each slot it has, one
 * root apiece, from the same table <App> renders. */
export function mountSlots(doc: Document = document): Root[] {
  const roots: Root[] = [];
  for (const [id, draw] of Object.entries(SLOTS)) {
    const el = doc.getElementById(id);
    if (!el) continue;
    const root = createRoot(el);
    flushSync(() => root.render(draw()));
    roots.push(root);
  }
  return roots;
}
