// CMX-221: Settings > Appearance, Collaboration and Notifications, ported from
// tests/term_theme_live.test.mjs (CMX-381), settings_term_size.test.mjs,
// settings_share_typing.test.mjs (CMX-403) and approvals_wiring's select half.
import { fireEvent } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { applySavedTheme, setTheme } from '../index.ts';
import { must, openOn, pick, posts, routes, settle, type, visible } from './setup.tsx';

// Panes: iframes whose shim may or may not have loaded (a not-yet-loaded one has no hook).
function panes(n: number, loaded = true) {
  const seen = { theme: 0, prefs: 0 };
  for (let i = 0; i < n; i++) {
    const f = document.createElement('iframe');
    document.body.append(f);
    const w = must(f.contentWindow) as Window & { chelaApplyTermTheme?: () => void; chelaApplyTermPrefs?: () => void };
    if (loaded) {
      w.chelaApplyTermTheme = () => { seen.theme++; };
      w.chelaApplyTermPrefs = () => { seen.prefs++; };
    }
  }
  return seen;
}
afterEach(() => document.querySelectorAll('iframe').forEach((f) => f.remove()));

test('setTheme stores the key the pane shims read, themes the page, and re-themes every open pane live', () => {
  const seen = panes(3);
  setTheme('warm');
  expect([localStorage.getItem('chela_theme'), document.body.dataset.theme, seen.theme]).toEqual(['warm', 'warm', 3]);
  setTheme('gruvbox');
  expect(seen.theme).toBe(6);
});

test('a pane whose shim has not loaded yet is skipped, not a throw (it reads the key on load)', () => {
  panes(1, false);
  const seen = panes(1);
  expect(() => setTheme('nord')).not.toThrow();
  expect(seen.theme).toBe(1);
});

test('the saved theme is on the page before anything paints from it; none is Dark', () => {
  localStorage.setItem('chela_theme', 'rose');
  applySavedTheme();
  expect(document.body.dataset.theme).toBe('rose');
  localStorage.clear();
  applySavedTheme();
  expect(document.body.dataset.theme).toBe('dark');
});

test('the theme picker offers the eight palettes, Warm among them, on the stored one', async () => {
  localStorage.setItem('chela_theme', 'midnight');
  await openOn('appearance');
  expect(must(document.getElementById('theme-select')).textContent).toBe('Midnight');
  fireEvent.keyDown(must(document.getElementById('theme-select')), { key: 'Enter' });
  expect([...document.querySelectorAll('[role="option"]')].map((o) => o.textContent))
    .toEqual(['Dark', 'Dim', 'Midnight', 'Nord', 'Gruvbox', 'Solarized', 'Rosé Pine', 'Warm']);
  fireEvent.click(must([...document.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent === 'Warm')));
  await settle(1);
  expect([localStorage.getItem('chela_theme'), document.body.dataset.theme]).toEqual(['warm', 'warm']);
  expect(must(document.getElementById('theme-select')).textContent).toBe('Warm');
});

test('the Size select offers 11px between 10 and 12; choosing it stores "11" and pushes it to every pane', async () => {
  const seen = panes(2);
  await openOn('appearance');
  fireEvent.keyDown(must(document.getElementById('term-size-select')), { key: 'Enter' });
  const opts = [...document.querySelectorAll('[role="option"]')].map((o) => o.textContent);
  expect(opts).toEqual(['8px', '10px', '11px', '12px', '13px', '14px', '15px', '16px', '18px']);
  fireEvent.click(must([...document.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent === '11px')));
  await settle(1);
  expect([localStorage.getItem('chela_term_fontsize'), seen.prefs]).toEqual(['11', 2]);
});

test('the stored faces and size render selected after a reload; unset ones read their defaults', async () => {
  localStorage.setItem('chela_term_fontsize', '11');
  localStorage.setItem('chela_term_latin', 'plex');
  await openOn('appearance');
  expect(must(document.getElementById('term-size-select')).textContent).toBe('11px');
  expect(must(document.getElementById('term-latin-select')).textContent).toBe('IBM Plex Mono');
  expect(must(document.getElementById('term-font-select')).textContent).toBe('Miriam Mono · aligned');
});

test.each([
  ['term-latin-select', 'Fira Code · ligatures', 'chela_term_latin', 'firacode'],
  ['term-font-select', 'Heebo · rounded', 'chela_term_font', 'heebo'],
])('%s stores its key and pushes the prefs to the panes', async (id, label, key, value) => {
  const seen = panes(1);
  await openOn('appearance');
  await pick(id, label);
  expect([localStorage.getItem(key), seen.prefs]).toEqual([value, 1]);
});

// --- Collaboration -------------------------------------------------------------------------

test('the collab name: stored per browser, trimmed; blank clears it (presence falls back to the auto-name)', async () => {
  localStorage.setItem('chela_collab_autoname', 'teal-otter');
  routes['GET /api/config'] = { collab_relay: 'wss://relay.example' };
  await openOn('collaboration');
  const input = must(document.getElementById('collab-name')) as HTMLInputElement;
  expect([input.value, input.placeholder, input.maxLength]).toEqual(['', 'teal-otter', 24]);
  expect(must(document.getElementById('collab-relay')).textContent).toBe('wss://relay.example');
  await type(input, '  Liav ');
  expect(localStorage.getItem('chela_collab_name')).toBe('Liav');
  await type(input, '   ');
  expect(localStorage.getItem('chela_collab_name')).toBeNull();
});

test('the relay says (default) when unset and (unavailable) when the config cannot load', async () => {
  await openOn('collaboration');
  expect(must(document.getElementById('collab-relay')).textContent).toBe('(default)');
  routes['GET /api/config'] = new Error('down');
  fireEvent.click(must(document.querySelector<HTMLElement>('.settings-modal-head [aria-label="Close"]')));
  await openOn('collaboration');
  expect(must(document.getElementById('collab-relay')).textContent).toBe('(unavailable)');
});

const share = () => must(document.getElementById('share-typing-toggle')) as HTMLButtonElement;

test('Guest typing sits in the Collaboration tab, OFF by default, and reflects share_typing=true', async () => {
  await openOn('collaboration');
  expect(must(share().closest('.settings-tabpanel')).getAttribute('data-tab')).toBe('collaboration');
  expect(share().getAttribute('aria-checked')).toBe('false');
  routes['GET /api/config'] = { share_typing: true, share_typing_source: 'dashboard' };
  fireEvent.click(must(document.querySelector<HTMLElement>('.settings-modal-head [aria-label="Close"]')));
  await openOn('collaboration');
  expect(share().getAttribute('aria-checked')).toBe('true');
  expect(must(document.getElementById('share-typing-source')).textContent).toBe('In effect: On (this setting).');
});

test('Guest typing env-locked: disabled, naming CHELA_SHARE_TYPING; a toggle POSTs {share_typing}', async () => {
  routes['GET /api/config'] = { share_typing: false, share_typing_env_locked: true };
  await openOn('collaboration');
  expect(share().disabled).toBe(true);
  expect(must(document.getElementById('share-typing-source')).textContent).toMatch(/set by CHELA_SHARE_TYPING; env wins/);
  routes['GET /api/config'] = { share_typing: false };
  routes['POST /api/config'] = { share_typing: true };
  fireEvent.click(must(document.querySelector<HTMLElement>('.settings-modal-head [aria-label="Close"]')));
  await openOn('collaboration');
  fireEvent.click(share());
  await settle();
  expect(posts('/api/config')).toEqual([{ share_typing: true }]);
  expect(must(document.getElementById('share-typing-msg')).textContent).toBe('Saved · guest typing allowed into sandboxed sessions');
});

test('search finds Guest typing by a keyword-only term and hides the unrelated rows', async () => {
  await openOn('general');
  fireEvent.change(must(document.getElementById('settings-search')), { target: { value: 'sandboxed container' } });
  await settle(1);
  expect(visible(must(share().closest('.field-row')))).toBe(true);
  expect(visible(document.getElementById('collab-name'))).toBe(false);
});

// --- Notifications -------------------------------------------------------------------------

test('Review toasts: Show by default; Muted stores the key work.js reads, Show clears it', async () => {
  await openOn('notifications');
  expect(must(document.getElementById('run-toasts-select')).textContent).toBe('Show');
  await pick('run-toasts-select', 'Muted');
  expect(localStorage.getItem('chela_mute_run_toasts')).toBe('1');
  await pick('run-toasts-select', 'Show');
  expect(localStorage.getItem('chela_mute_run_toasts')).toBeNull();
});

test('Approval requests: off by default; on asks the browser and stores it; a refused permission stays off', async () => {
  let answer = 'granted';
  vi.stubGlobal('Notification', { permission: 'default', requestPermission: () => Promise.resolve(answer) });
  await openOn('notifications');
  const sel = () => must(document.getElementById('approval-notify-select'));
  expect(sel().textContent).toBe('Off');
  await pick('approval-notify-select', 'Browser notification');
  expect([localStorage.getItem('chela_approval_notify'), sel().textContent]).toEqual(['1', 'Browser notification']);
  await pick('approval-notify-select', 'Off');
  expect(localStorage.getItem('chela_approval_notify')).toBeNull();
  answer = 'denied';
  await pick('approval-notify-select', 'Browser notification');
  expect([localStorage.getItem('chela_approval_notify'), sel().textContent]).toEqual([null, 'Off']);
});
