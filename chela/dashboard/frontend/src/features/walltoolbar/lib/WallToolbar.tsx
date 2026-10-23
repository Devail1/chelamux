// CMX-242 (CMX-212 P30): the Wall's layout toolbar: the fill presets, lock (a drag swaps two
// panes, resize off), auto-arrange and Focus. It writes shared/store/wall.ts `wallLayout`; the
// legacy grid code reads it and applies each change. The row folds to ONE control showing the
// current layout's glyph plus a chevron (CMX-397): it rests folded while Focus is on unless the
// user's own choice (a click on that control) says otherwise, and choosing a preset or a mode, a
// click outside or Esc folds it again WITHOUT rewriting that choice. From terminals.js
// `_buildGridPicker` and the toggles around it.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { gridRowCollapsed } from '../../../../../static/js/wallmodel.ts';
import { icons } from '../../../shared/icons/index.ts';
import { useStore } from '../../../shared/store/index.ts';
import { setWallAuto, setWallFocus, setWallLocked, setWallPreset, termMode, wallLayout, type WallLayout } from '../../../shared/store/wall.ts';
import { IconButton, TextButton } from '../../../shared/ui/index.ts';
import { WALL_PRESETS, type Preset } from '../model.ts';

const ROW_KEY = 'pc_wall_grid_collapsed';

/** A cols×rows grid as rounded rects on an 18×14 canvas: the preset's shape at a glance. */
export function GridGlyph({ cols, rows }: { cols: number; rows: number }) {
  const W = 18, H = 14, m = 1, g = 1.5;
  const cw = (W - 2 * m - (cols - 1) * g) / cols;
  const ch = (H - 2 * m - (rows - 1) * g) / rows;
  const rects: ReactNode[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      rects.push(<rect key={`${r}-${c}`} x={(m + c * (cw + g)).toFixed(2)} y={(m + r * (ch + g)).toFixed(2)} width={cw.toFixed(2)} height={ch.toFixed(2)} rx="1" />);
    }
  }
  return <svg viewBox={`0 0 ${W} ${H}`} width="18" height="14" fill="currentColor" aria-hidden="true">{rects}</svg>;
}

/** Focus: one large pane beside a strip of two. A shape of its own, never a preset variant. */
export function FocusGlyph() {
  return (
    <svg viewBox="0 0 18 14" width="18" height="14" fill="currentColor" aria-hidden="true">
      <rect x="1" y="1" width="10" height="12" rx="1" />
      <rect x="13" y="1" width="4" height="5" rx="1" />
      <rect x="13" y="8" width="4" height="5" rx="1" />
    </svg>
  );
}

const AutoGlyph = () => <icons.autoArrange size={14} aria-hidden />;

/** What owns the layout now: Focus, then auto-arrange, then the preset (the default if unknown). */
export function currentLayout(l: WallLayout): { glyph: ReactNode; label: string } {
  if (l.focus) return { glyph: <FocusGlyph />, label: 'Focus' };
  if (l.auto) return { glyph: <AutoGlyph />, label: 'Auto-arrange' };
  const p = WALL_PRESETS.find((q) => q.cols === l.preset.cols && q.rows === l.preset.rows) ?? (WALL_PRESETS[1] as Preset);
  return { glyph: <GridGlyph cols={p.cols} rows={p.rows} />, label: p.label };
}

function storedChoice(): string | null {
  try {
    return localStorage.getItem(ROW_KEY);
  } catch {
    return null;
  }
}

const LOCK_TITLE = {
  on: 'Layout locked: drag a pane onto another to swap them. Click to unlock (free move + resize).',
  off: 'Lock layout: drag swaps panes, resize off',
};
const AUTO_TITLE = {
  on: 'Auto-arrange ON: panes rank by attention (needs you → working → idle → done). Click to restore your manual layout.',
  off: 'Auto-arrange: rank panes by attention (needs you → working → idle → done)',
};
const FOCUS_TITLE = {
  on: 'Focus ON: one pane large, the rest a strip. Click a strip pane’s header to promote it. Click to restore your manual layout.',
  off: 'Focus: one pane large, the rest a strip',
};

export function WallToolbar() {
  const mode = useStore(termMode);
  const layout = useStore(wallLayout);
  // The rest state is read ONCE, when the toolbar first draws.
  const [open, setOpen] = useState(() => !gridRowCollapsed(storedChoice(), wallLayout.get().focus));
  const bar = useRef<HTMLSpanElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);

  // Transient folds: a click outside, or Esc (which hands focus back to the toggle rather than
  // leaving it on a control that just disappeared).
  useEffect(() => {
    if (!open) return;
    const down = (e: PointerEvent) => {
      if (bar.current && !bar.current.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      const inside = !!bar.current && bar.current.contains(document.activeElement);
      setOpen(false);
      if (inside) toggle.current?.focus();
    };
    document.addEventListener('pointerdown', down);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('pointerdown', down);
      document.removeEventListener('keydown', key);
    };
  }, [open]);

  // The toggle is the one stated preference, so it alone is remembered.
  const flip = () => {
    const next = !open;
    setOpen(next);
    try {
      localStorage.setItem(ROW_KEY, next ? '0' : '1');
    } catch {
      /* storage unavailable */
    }
  };

  const { glyph, label } = currentLayout(layout);
  const toggleTitle = `Layout: ${label}, ${open ? 'hide' : 'show'} the layout toolbar`;
  return (
    // wall mode only: single mode (every phone) has one pane and nothing to lay out
    <span id="term-wall-grid" ref={bar} hidden={mode !== 'wall'}>
      <IconButton id="term-grid-toggle" ref={toggle} className={open ? 'gl-btn grid-open' : 'gl-btn'} aria-expanded={open} aria-controls="term-grid-row"
        label={toggleTitle} onClick={flip}>
        {glyph}
        <span className="grid-chevron" aria-hidden="true">
          <icons.chevronDown size={12} />
        </span>
      </IconButton>
      <span id="term-grid-row" hidden={!open}>
        <span className="gl-label">Grid:</span>
        <span id="term-grid-presets">
          {WALL_PRESETS.map((p, i) => (
            <TextButton key={p.label} className={p.cols === layout.preset.cols && p.rows === layout.preset.rows ? 'gl-btn active' : 'gl-btn'}
              data-preset={i} data-cols={p.cols} data-rows={p.rows} title={p.label} aria-label={p.label}
              onClick={() => {
                setWallPreset({ cols: p.cols, rows: p.rows });
                setOpen(false);
              }}>
              <GridGlyph cols={p.cols} rows={p.rows} />
            </TextButton>
          ))}
        </span>
        <TextButton id="term-lock-btn" className={layout.locked ? 'gl-btn active' : 'gl-btn'} aria-pressed={layout.locked}
          title={layout.locked ? LOCK_TITLE.on : LOCK_TITLE.off} aria-label="Lock layout"
          onClick={(e) => {
            setWallLocked(!layout.locked);
            e.currentTarget.blur();
          }}>
          {layout.locked ? <icons.layoutLocked size={14} aria-hidden /> : <icons.layoutUnlocked size={14} aria-hidden />}
        </TextButton>
        {/* Auto-arrange and Focus both OWN the layout: the store turns the other off */}
        <TextButton id="term-auto-btn" className={layout.auto ? 'gl-btn active' : 'gl-btn'} aria-pressed={layout.auto}
          title={layout.auto ? AUTO_TITLE.on : AUTO_TITLE.off} aria-label="Auto-arrange"
          onClick={() => {
            setWallAuto(!layout.auto);
            setOpen(false);
          }}>
          <AutoGlyph />
        </TextButton>
        <TextButton id="term-focus-btn" className={layout.focus ? 'gl-btn active' : 'gl-btn'} aria-pressed={layout.focus}
          title={layout.focus ? FOCUS_TITLE.on : FOCUS_TITLE.off} aria-label="Focus"
          onClick={() => {
            setWallFocus(!layout.focus);
            setOpen(false);
          }}>
          <FocusGlyph />
        </TextButton>
      </span>
    </span>
  );
}
