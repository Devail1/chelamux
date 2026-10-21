// CMX-278: the desktop sidebar's width handle. Dragging sets the column live (style.css
// `--sidebar-w`); letting go saves it to the server layout, so it follows across devices.
// Arrow keys step it, Home / End go to the bounds, a double-click restores the default.
// Hidden on the phone drawer and the collapsed rail (style.css).
import { useRef, type KeyboardEvent, type PointerEvent } from 'react';
import { SIDEBAR_WIDTH, clampWidth } from '../model.ts';

const STEP = 16;
const KEY_TO: Record<string, (w: number) => number> = {
  ArrowLeft: w => w - STEP, ArrowRight: w => w + STEP, Home: () => SIDEBAR_WIDTH.min, End: () => SIDEBAR_WIDTH.max,
};

export function ResizeHandle({ width, onCommit }: { width: number | null; onCommit: (w: number) => void }) {
  const w = clampWidth(width);
  const drag = useRef<{ x0: number; w0: number; w: number } | null>(null);
  const live = (px: number) => document.documentElement.style.setProperty('--sidebar-w', `${px}px`);
  const down = (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture?.(e.pointerId); // a pane's iframe must not swallow the drag
    drag.current = { x0: e.clientX, w0: w, w };
    document.body.classList.add('sidebar-resizing');
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    d.w = clampWidth(d.w0 + e.clientX - d.x0);
    live(d.w);
  };
  const up = () => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    document.body.classList.remove('sidebar-resizing');
    onCommit(d.w);
  };
  const key = (e: KeyboardEvent<HTMLDivElement>) => {
    const to = KEY_TO[e.key];
    if (!to) return;
    e.preventDefault();
    onCommit(clampWidth(to(w)));
  };
  return (
    <div className="sidebar-resize" role="separator" aria-orientation="vertical" aria-label="Sidebar width"
      aria-valuemin={SIDEBAR_WIDTH.min} aria-valuemax={SIDEBAR_WIDTH.max} aria-valuenow={w} tabIndex={0}
      title="Drag to resize the sidebar. Double-click for the default width."
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onKeyDown={key}
      onDoubleClick={() => onCommit(SIDEBAR_WIDTH.default)} />
  );
}
