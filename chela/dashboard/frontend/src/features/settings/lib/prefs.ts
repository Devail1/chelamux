// CMX-221 (CMX-212 P15): the per-browser prefs Settings writes: the theme, the terminal fonts
// and size, the run-toast mute and the collab name. Each ttyd iframe is same-origin and reads
// the same keys (app.py's theme and font shims, collab presence), so writing one fires a
// `storage` event in every pane; the theme and fonts ALSO poke each open pane directly, so the
// change is live in the frame that made it too. No ttyd restart.
import { MUTE_KEY } from '../../runtoast/model.ts';
import { lsRead, lsRemove, lsSet } from './storage.ts';

export const KEYS = {
  theme: 'chela_theme',
  latin: 'chela_term_latin',
  font: 'chela_term_font',
  size: 'chela_term_fontsize',
  muteToasts: MUTE_KEY,
  collabName: 'chela_collab_name',
  collabAuto: 'chela_collab_autoname',
  costView: 'chela_cost_view',
  costWindow: 'chela_cost_window',
  usageWindow: 'chela_usage_window',
} as const;

export const DEFAULTS = { theme: 'dark', latin: 'jetbrains', font: 'miriam', size: '14' } as const;

type PaneHooks = { chelaApplyTermTheme?: () => void; chelaApplyTermPrefs?: () => void };

/** Call `hook` in every pane that has loaded its shim; a pane that has not reads the key on load. */
function pokePanes(hook: keyof PaneHooks, doc: Document = document): void {
  doc.querySelectorAll('iframe').forEach((f) => {
    try {
      const w = f.contentWindow as (Window & PaneHooks) | null;
      const fn = w?.[hook];
      if (typeof fn === 'function') fn();
    } catch { /* not loaded yet, or not same-origin: its shim reads the key on load */ }
  });
}

/** CMX-381: a theme covers the terminals too (each pane carries every theme's palette). */
export function setTheme(t: string, doc: Document = document): void {
  lsSet(KEYS.theme, t);
  doc.body.dataset.theme = t;
  pokePanes('chelaApplyTermTheme', doc);
}

/** The saved theme, on the page before anything paints from it. */
export function applySavedTheme(doc: Document = document): void {
  doc.body.dataset.theme = lsRead(KEYS.theme) || DEFAULTS.theme;
}

export function setTermPref(key: 'latin' | 'font' | 'size', v: string): void {
  lsSet(KEYS[key], v);
  pokePanes('chelaApplyTermPrefs');
}

/** Mute or unmute the dispatcher run-state review toasts (features/runtoast reads the key). */
export function setRunToastsMuted(muted: boolean): void {
  if (muted) lsSet(KEYS.muteToasts, '1');
  else lsRemove(KEYS.muteToasts);
}

/** The collab display name. Empty clears it: presence falls back to the persisted auto-name. */
export function setCollabName(v: string): void {
  const name = v.trim();
  if (name) lsSet(KEYS.collabName, name);
  else lsRemove(KEYS.collabName);
}
