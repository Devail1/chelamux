// The keybar island: #keybar-island, under the Wall's stage.
import { createRoot, type Root } from 'react-dom/client';
import { termKey, termPaste } from '../../paneframe/index.ts';
import { Keybar, type KeybarActions } from './Keybar.tsx';

/** The single pane's keys and Paste (features/paneframe, CMX-246). */
export const paneKeybarActions: KeybarActions = {
  termKey: (key) => void termKey(key),
  termPaste: (btn) => void termPaste(btn),
};

/** Mount into `#keybar-island`; null when the page has none (terminals off). */
export function mountKeybar(win: Window, actions = paneKeybarActions): Root | null {
  const el = win.document.getElementById('keybar-island');
  if (!el) return null;
  const root = createRoot(el);
  root.render(<Keybar actions={actions} />);
  return root;
}
