// CMX-221 (CMX-212 P15): the Settings modal (CMX-287), ported from nav.js's Settings region and
// index.html's #settings-drawer. A tab rail (General, Timing, Dispatch, Notifications, Cost,
// Appearance, Collaboration) beside the panels, and a VS Code-style search over all of them
// (CMX-396). Every panel is rendered while the modal is open, so search reaches every tab.
import { useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useStore } from '../../../shared/store/index.ts';
import { Dialog, TextButton, TextInput } from '../../../shared/ui/index.ts';
import { VersionBadge } from '../../version/index.ts';
import { SETTINGS_TABS, countText, searchTerms, type SettingsTab } from '../model.ts';
import { Appearance } from './Appearance.tsx';
import { Collaboration } from './Collaboration.tsx';
import { Cost } from './Cost.tsx';
import { Dispatch } from './Dispatch.tsx';
import { General } from './General.tsx';
import { Notifications } from './Notifications.tsx';
import { applySearch } from './search.ts';
import { closeSettings, selectSettingsTab, setSettingsQuery, settings } from './state.ts';
import { Timing } from './Timing.tsx';

const PANELS: Record<SettingsTab, () => React.JSX.Element> = {
  general: General, timing: Timing, dispatch: Dispatch, notifications: Notifications,
  cost: Cost, appearance: Appearance, collaboration: Collaboration,
};

/** Ctrl/⌘+, lands in the search box, like VS Code. Not on a touch screen: the on-screen
 * keyboard would cover the modal on every open. */
const coarse = () => typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches;

function Rail({ tab, searching }: { tab: SettingsTab; searching: boolean }) {
  const strip = useRef<HTMLDivElement>(null);
  // CMX-57: at phone width the rail is a horizontal strip that scrolls on its own (never the
  // modal), and says so: .fade-start / .fade-end while tabs are hidden off that edge. The
  // selected tab is scrolled into the strip (not scrollIntoView: that scrolls every ancestor).
  useLayoutEffect(() => {
    const el = strip.current;
    if (!el) return;
    const sync = () => {
      const max = el.scrollWidth - el.clientWidth;
      el.classList.toggle('fade-start', max > 1 && el.scrollLeft > 1);
      el.classList.toggle('fade-end', max > 1 && el.scrollLeft < max - 1);
    };
    const on = el.querySelector(`.settings-tab[data-tab="${tab}"]`);
    if (on && !searching && el.scrollWidth > el.clientWidth) {
      const r = el.getBoundingClientRect(), t = on.getBoundingClientRect();
      if (t.left < r.left) el.scrollLeft -= r.left - t.left + 24;
      else if (t.right > r.right) el.scrollLeft += t.right - r.right + 24;
    }
    sync();
    el.addEventListener('scroll', sync, { passive: true });
    window.addEventListener('resize', sync);
    return () => {
      el.removeEventListener('scroll', sync);
      window.removeEventListener('resize', sync);
    };
  }, [tab, searching]);
  // One tab stop for the rail (the selected tab), arrows move along it; none while searching,
  // so Tab goes from the search box straight to the filtered controls.
  const move = (e: ReactKeyboardEvent) => {
    const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const i = SETTINGS_TABS.findIndex((t) => t.id === tab);
    const next = SETTINGS_TABS[(i + step + SETTINGS_TABS.length) % SETTINGS_TABS.length]?.id ?? tab;
    selectSettingsTab(next);
    strip.current?.querySelector<HTMLElement>(`.settings-tab[data-tab="${next}"]`)?.focus();
  };
  return (
    <nav className="settings-tabs" id="settings-tabs" ref={strip} aria-label="Settings sections" onKeyDown={move}>
      {SETTINGS_TABS.map((t) => {
        const on = !searching && t.id === tab;
        return (
          <TextButton key={t.id} className={on ? 'settings-tab active' : 'settings-tab'} data-tab={t.id}
            aria-current={on ? 'page' : undefined} tabIndex={on ? 0 : -1} onClick={() => selectSettingsTab(t.id)}>
            {t.label}
          </TextButton>
        );
      })}
    </nav>
  );
}

export function Settings() {
  const s = useStore(settings);
  const body = useRef<HTMLDivElement>(null);
  const [count, setCount] = useState<number | null>(null);
  const searching = searchTerms(s.query).length > 0;
  // Re-mark on every query, and while one is active on every change under the panels (a tab's
  // rows land after their fetch): a late row is filtered like the rest.
  useLayoutEffect(() => {
    const el = body.current;
    if (!el) return;
    const run = () => setCount(applySearch(el, s.query));
    run();
    if (!searching) return;
    const mo = new MutationObserver(run);
    mo.observe(el, { childList: true, subtree: true, characterData: true });
    return () => mo.disconnect();
  }, [s.query, s.open, searching]);
  const status = count === null ? '' : countText(count, s.query);
  return (
    <Dialog open={s.open} onOpenChange={(o) => { if (!o) closeSettings(); }} title="Settings" variant="settings" id="settings-drawer"
      className={searching ? 'searching' : undefined}
      onEscapeKeyDown={(e) => {
        // with a query in the box, the first Esc clears it; Esc on an empty one closes
        if (s.query) {
          e.preventDefault();
          setSettingsQuery('');
        }
      }}>
      <div className="settings-search">
        <TextInput search plain id="settings-search" label="Search settings" placeholder="Search settings" value={s.query} onChange={setSettingsQuery} autoFocus={!coarse()} />
        <p className="settings-search-empty" id="settings-search-empty" hidden={count !== 0}>{count === 0 ? status : ''}</p>
        <span className="sr-only" id="settings-search-status" role="status" aria-live="polite">{status}</span>
      </div>
      <div className="settings-modal-body">
        <Rail tab={s.tab} searching={searching} />
        <div className="settings-tabpanels" id="drawer-body" ref={body}>
          {SETTINGS_TABS.map((t) => {
            const Panel = PANELS[t.id];
            return (
              <div key={t.id} className={t.id === s.tab ? 'settings-tabpanel active' : 'settings-tabpanel'} data-tab={t.id}>
                {searching && <h3 className="settings-search-group">{t.label}</h3>}
                <Panel />
              </div>
            );
          })}
        </div>
      </div>
      {/* AGPL §13: a user interacting with this dashboard over a network is offered the
          Corresponding Source, here where every user who opens Settings sees it. */}
      <div className="settings-modal-foot">
        chelamux is licensed under <a href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noopener">AGPL v3</a>.{' '}
        <a href="https://github.com/Devail1/chelamux" target="_blank" rel="noopener">Source code</a>.
        {/* CMX-291: the build string lives here; in the sidebar foot it drew over the resources strip. */}
        <span id="version-island"><VersionBadge /></span>
      </div>
    </Dialog>
  );
}
