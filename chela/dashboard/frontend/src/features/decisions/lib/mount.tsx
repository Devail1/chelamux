// The inbox island: #decisions-island, in the sidebar head. One root draws the button; the
// dialog portals itself to <body>.
import { createRoot, type Root } from 'react-dom/client';
import { DecisionsButton, type JumpToOrchestrator } from './DecisionsButton.tsx';
import { DecisionsDialog } from './DecisionsDialog.tsx';

/** nav.js's jumpToOrchestrator, looked up on `win.chela` at call time (it loads after us). */
export function legacyJump(win: Window): JumpToOrchestrator {
  return () => (win as unknown as { chela?: { jumpToOrchestrator?: () => unknown } }).chela?.jumpToOrchestrator?.();
}

/** Mount into `#decisions-island`; null when the page has none. */
export function mountDecisions(win: Window, jump = legacyJump(win)): Root | null {
  const el = win.document.getElementById('decisions-island');
  if (!el) return null;
  const root = createRoot(el);
  root.render(
    <>
      <DecisionsButton jump={jump} />
      <DecisionsDialog />
    </>,
  );
  return root;
}
