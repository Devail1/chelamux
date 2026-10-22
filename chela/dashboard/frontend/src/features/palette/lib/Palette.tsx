// CMX-221 (CMX-212 P19): the ⌘K palette and the keyboard shortcuts sheet (CMX-121), ported from
// nav.js and index.html's #palette / #shortcuts-overlay.
import { Fragment, useLayoutEffect, useRef } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { agents } from '../../../shared/store/agents.ts';
import { launcher } from '../../../shared/store/launcher.ts';
import { useStore } from '../../../shared/store/index.ts';
import { shares } from '../../share/index.ts';
import { Dialog, TextButton, TextInput } from '../../../shared/ui/index.ts';
import { closePalette, closeShortcuts, hoverPalette, palette, paletteRows, runPalette, setDrawnRows, setPaletteQuery, shortcuts } from './state.ts';

export function Palette() {
  const s = useStore(palette);
  // the rows read these: a change redraws them while the palette is open
  useStore(agents);
  useStore(launcher);
  useStore(shares);
  const list = useRef<HTMLDivElement>(null);
  const { items, panes } = s.open ? paletteRows(s.query) : { items: [], panes: 0 };
  const sel = s.sel < items.length ? s.sel : 0;
  useLayoutEffect(() => {
    setDrawnRows(s.open ? items : null);
  });
  useLayoutEffect(() => {
    list.current?.querySelector('.palette-item.sel')?.scrollIntoView?.({ block: 'nearest' });
  }, [sel, s.open]);
  return (
    <Dialog open={s.open} onOpenChange={(o) => { if (!o) closePalette(); }} title="Command palette" hideTitle variant="palette" id="palette" keepFocus={s.fromSidebar}>
      <TextInput className="palette-input" id="palette-input" plain label="Jump to an agent, view, or project" placeholder="Jump to an agent, view, or project…"
        value={s.query} onChange={setPaletteQuery} autoFocus={!s.fromSidebar} />
      <div className="palette-list" id="palette-list" role="listbox" aria-label="Results" ref={list}>
        {items.map((it, i) => {
          const Icon = it.icon ? icons[it.icon] : null;
          return (
            <Fragment key={i}>
              {/* CMX-116: the open panes, floated first on an empty query, end here */}
              {panes > 0 && i === panes && <div className="palette-divider" role="separator" />}
              <TextButton className={i === sel ? 'palette-item sel' : 'palette-item'} data-i={i} role="option" aria-selected={i === sel} tabIndex={-1}
                onMouseEnter={() => hoverPalette(i)} onClick={() => runPalette(i)}>
                <span className="pi-icon">
                  {it.dot ? <span className={`term-status-dot ${it.dot}`} /> : <span className="pi-glyph">{Icon && <Icon aria-hidden size={14} />}</span>}
                </span>
                <span className="pi-title">{it.title}</span>
                <span className="pi-sub">{it.sub}</span>
              </TextButton>
            </Fragment>
          );
        })}
        {!items.length && <div className="palette-empty">No matches</div>}
      </div>
      <div className="palette-foot"><span><kbd>↑</kbd><kbd>↓</kbd> navigate</span><span><kbd>↵</kbd> open</span><span><kbd>esc</kbd> close</span></div>
    </Dialog>
  );
}

const Keys = ({ keys }: { keys: string[] }) => (
  <span className="sc-keys">{keys.map((k, i) => (k === '+' || k === '/' || k === '…' ? <span key={i} className="sc-plus">{k}</span> : <kbd key={i}>{k}</kbd>))}</span>
);

/** One shortcut: its keys, or the gesture that does it. */
function Row({ label, keys, gesture }: { label: string; keys?: string[]; gesture?: string }) {
  return (
    <div className="sc-row">
      <span className="sc-lbl">{label}</span>
      {keys ? <Keys keys={keys} /> : <span className="sc-gesture">{gesture}</span>}
    </div>
  );
}

/** CMX-121: reachable ONLY from the palette's "Keyboard shortcuts" row. Deliberately no global
 * key: Alt+/ was dropped, Ctrl+M is Enter inside a focused terminal. Static content. */
export function Shortcuts() {
  const open = useStore(shortcuts);
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) closeShortcuts(); }} title="Keyboard shortcuts" variant="shortcuts" icon={icons.keyboard} id="shortcuts-overlay">
      <div className="shortcuts-cols">
        <div className="sc-grp">
          <h4>Navigate panes</h4>
          <Row label="Open sidebar session N (hold Alt to see N)" keys={['Alt', '+', '1', '…', '9']} />
          <Row label="Command palette / switcher" keys={['Ctrl', '/', '⌘', '+', 'K']} />
          <Row label="Open / close Settings" keys={['Ctrl', '/', '⌘', '+', ',']} />
        </div>
        <div className="sc-grp">
          <h4>In the palette</h4>
          <Row label="Move selection" keys={['↑', '↓']} />
          <Row label="Open / run" keys={['Enter']} />
          <Row label="Close" keys={['Esc']} />
        </div>
        <div className="sc-grp">
          <h4>Panes &amp; wall</h4>
          <Row label="Rename a pane" gesture="double-click name" />
          <Row label="Cancel a wire drag" keys={['Esc']} />
          <Row label="Wire · Share · Orchestrator · Pin" gesture="⋯ menu" />
          <Row label="Grid preset · lock · min/max/kill" gesture="toolbar / header" />
        </div>
        <div className="sc-grp">
          <h4>Help</h4>
          <Row label="This shortcuts map" gesture="⌘K → Keyboard shortcuts" />
        </div>
      </div>
      <div className="shortcuts-foot">Grouped, colorblind-safe (chips + labels, no hue coding).</div>
    </Dialog>
  );
}
