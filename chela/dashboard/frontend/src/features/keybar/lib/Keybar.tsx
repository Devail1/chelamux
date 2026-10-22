// The on-screen keys under the single pane (CMX-237, FRONTEND_PORT_PLAN.md P29): the
// collapsible desktop key bar (#term-bar) and the phone's persistent accessory row
// (#term-keybar, keybar v2, modeled on Moshi's terminal keyboard). Keys go through
// features/paneframe's termKey() to /api/term/key; Paste through its termPaste().
import { useEffect, useRef, useState } from 'react';
import { icons } from '../../../shared/icons/index.ts';
import { termMode } from '../../../shared/store/wall.ts';
import { useStore } from '../../../shared/store/index.ts';
import { ARROWS, ARROW_FACE, type Arrow, CHARS, CTRL_LETTERS, type CtrlState, DESK_NAV, type Key, PAGING, afterCtrlLetter, nextCtrl } from '../model.ts';
import { bindKeybarPin } from './pin.ts';

export interface KeybarActions {
  termKey: (key: string) => void;
  termPaste: (btn: HTMLButtonElement) => void;
}

/** localStorage's remembered collapse; collapsed unless the user opened it. */
const COLLAPSED_KEY = 'pc_kb_collapsed';
function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) !== '0';
  } catch {
    return true;
  }
}

function KeyButton({ k, className, onPress }: { k: Key; className?: string; onPress: () => void }) {
  return (
    <button type="button" className={className} title={k.title} aria-label={k.label} onClick={onPress}>
      {k.face}
    </button>
  );
}

/** Desktop (and wide tablets): a sticky dock under the single pane, collapsed by default. */
function DeskBar({ actions }: { actions: KeybarActions }) {
  const single = useStore(termMode) === 'single';
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [scrolling, setScrolling] = useState(false);
  const send = (key: string) => () => actions.termKey(key);
  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0');
    } catch {
      /* private mode: the choice lasts this page only */
    }
  };
  const scroll = () => {
    actions.termKey(scrolling ? 'scroll-exit' : 'scroll');
    setScrolling(!scrolling);
  };
  return (
    <div id="term-bar" className={collapsed ? 'kb-collapsed' : undefined} style={{ display: single ? 'flex' : 'none' }}>
      <button className="kb-toggle" id="kb-toggle" type="button" onClick={toggle} aria-expanded={!collapsed} aria-controls="kb-body" title="Show/hide on-screen keys">
        <span>
          <icons.keyboard aria-hidden size={14} /> Keys
        </span>
        <icons.chevronUp className="kb-chevron" aria-hidden size={12} />
      </button>
      <div className="kb-body" id="kb-body">
        <div className="kb-row">
          <div className="kb-group" aria-label="Special keys">
            <KeyButton k={{ key: 'Escape', face: 'Esc', title: 'Escape' }} onPress={send('Escape')} />
            <KeyButton k={{ key: 'Tab', face: 'Tab', title: 'Tab' }} onPress={send('Tab')} />
            <KeyButton k={{ key: 'Enter', face: '⏎', title: 'Enter', label: 'Enter' }} onPress={send('Enter')} />
          </div>
          <div className="kb-group" aria-label="Control keys">
            <KeyButton k={{ key: 'C-c', face: '^C', title: 'Ctrl-C (interrupt)' }} onPress={send('C-c')} />
            <KeyButton k={{ key: 'C-d', face: '^D', title: 'Ctrl-D (EOF)' }} onPress={send('C-d')} />
            <KeyButton k={{ key: 'C-l', face: '^L', title: 'Ctrl-L (clear)' }} onPress={send('C-l')} />
            <KeyButton k={{ key: 'C-b', face: '^B', title: 'Ctrl-B (tmux prefix)' }} onPress={send('C-b')} />
          </div>
          <div className="kb-group" aria-label="Scrollback">
            <button id="term-scroll-btn" type="button" className={scrolling ? 'btn-accent' : undefined} onClick={scroll}>
              {scrolling ? (
                'Exit Scroll'
              ) : (
                <>
                  <icons.scrollback aria-hidden size={14} /> Scroll
                </>
              )}
            </button>
          </div>
        </div>
        <div className="kb-row">
          <div className="kb-dpad" aria-label="Arrow keys">
            {(['Up', 'Left', 'Down', 'Right'] as const satisfies Arrow[]).map((a) => (
              <KeyButton key={a} k={{ key: a, face: ARROW_FACE[a], title: a, label: a }} className={`kb-${a.toLowerCase()}`} onPress={send(a)} />
            ))}
          </div>
          <div className="kb-nav" aria-label="Navigation keys">
            {DESK_NAV.map((k) => (
              <KeyButton key={k.key} k={k} onPress={send(k.key)} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Phone: a persistent, horizontally scrollable row pinned above the soft keyboard. Special
 * characters are one tap; Ctrl is a sticky/lock modifier revealing a Ctrl-letter layer. */
function PhoneBar({ actions }: { actions: KeybarActions }) {
  const [ctrl, setCtrl] = useState<CtrlState>('off');
  const bar = useRef<HTMLDivElement>(null);
  useEffect(() => (bar.current ? bindKeybarPin(bar.current) : undefined), []);
  const key = (k: Key, className = 'kb2-key') => (
    <KeyButton key={k.key} k={k} className={className} onPress={() => actions.termKey(k.key)} />
  );
  const sep = (n: number) => <span key={`sep${n}`} className="kb2-sep" aria-hidden="true" />;
  return (
    <div id="term-keybar" className="term-keybar" data-ctrl={ctrl} ref={bar}>
      {key({ key: 'Escape', face: 'Esc' })}
      {key({ key: 'Tab', face: 'Tab' })}
      {key({ key: 'BTab', face: '⇧Tab', title: 'Shift-Tab (back-tab · toggle agent mode)', label: 'Shift Tab' })}
      {key({ key: 'Enter', face: '⏎', label: 'Enter' }, 'kb2-key kb2-go')}
      <button type="button" className="kb2-key" title="Paste from clipboard" onClick={(e) => actions.termPaste(e.currentTarget)}>
        Paste
      </button>
      {sep(1)}
      {key({ key: 'C-c', face: '^C', title: 'Ctrl-C (interrupt)' }, 'kb2-key kb2-strong')}
      <button
        id="kb2-ctrl"
        type="button"
        className="kb2-key kb2-mod"
        aria-pressed={ctrl !== 'off'}
        onClick={() => setCtrl(nextCtrl)}
        title="Ctrl (tap: arm for next key · tap again: lock · again: off)"
      >
        Ctrl
      </button>
      <span className="kb2-ctrl-letters" id="kb2-ctrl-letters">
        {CTRL_LETTERS.map((k) => (
          <KeyButton
            key={k.key}
            k={k}
            className="kb2-key kb2-cl"
            onPress={() => {
              actions.termKey(`C-${k.key}`);
              setCtrl(afterCtrlLetter);
            }}
          />
        ))}
      </span>
      {sep(2)}
      {ARROWS.map((k) => key(k))}
      {sep(3)}
      {CHARS.map((k) => key(k))}
      {sep(4)}
      {PAGING.map((k) => key(k))}
    </div>
  );
}

/** The island: both bars (CSS shows one per viewport). */
export function Keybar({ actions }: { actions: KeybarActions }) {
  return (
    <>
      <DeskBar actions={actions} />
      <PhoneBar actions={actions} />
    </>
  );
}
