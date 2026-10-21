// The keybar island: #keybar-island, under the Wall's stage.
import type { Root } from 'react-dom/client';
import { createIslandRoot } from '../../../shared/islands/index.tsx';
import { Keybar, type KeybarActions } from './Keybar.tsx';

/** terminals.js's handlers, looked up on `win.chela` at call time (it loads after the island). */
export function legacyKeybarActions(win: Window): KeybarActions {
  const chela = () => (win as unknown as { chela?: Partial<KeybarActions> }).chela;
  return {
    termKey: (key) => chela()?.termKey?.(key),
    termPaste: (btn) => chela()?.termPaste?.(btn),
  };
}

/** Mount into `#keybar-island`; null when the page has none (terminals off). */
export function mountKeybar(win: Window, actions = legacyKeybarActions(win)): Root | null {
  const el = win.document.getElementById('keybar-island');
  if (!el) return null;
  const root = createIslandRoot(el);
  root.render(<Keybar actions={actions} />);
  return root;
}
