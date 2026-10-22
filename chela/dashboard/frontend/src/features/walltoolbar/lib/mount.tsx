// CMX-242 (CMX-212 P30): the toolbar's root, in the slot index.html keeps for it in the Wall's
// top row. terminals.js mounts it as it renders (the first paint is synchronous, so the row is
// whole by the time the grid measures the stage under it).
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import { WallToolbar } from './WallToolbar.tsx';

const roots = new WeakMap<HTMLElement, Root>();

/** Draw the toolbar into `slot` (idempotent for the same element). */
export function mountWallToolbar(slot: HTMLElement | null): void {
  if (!slot || roots.has(slot)) return;
  const root = createRoot(slot);
  roots.set(slot, root);
  flushSync(() => root.render(<WallToolbar />));
}
