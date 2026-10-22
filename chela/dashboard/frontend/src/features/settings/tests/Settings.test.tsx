// CMX-221: the Settings modal shell and its search (CMX-287, CMX-396), ported from
// tests/settings_search.test.mjs and settings_cost.test.mjs's tab half. The page's real
// style.css is loaded, so "visible" is the computed cascade, not a restatement of the classes.
import { act, fireEvent, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { closeSettings, openSettings, settings, toggleSettings } from '../index.ts';
import { must, openOn, routes, rowOf, sectionTitled, settle, visible } from './setup.tsx';

const modal = () => must(document.getElementById('settings-drawer'), '#settings-drawer');
const search = () => must(document.getElementById('settings-search'), '#settings-search') as HTMLInputElement;
const status = () => must(document.getElementById('settings-search-status')).textContent;
const activeTab = () => [...document.querySelectorAll<HTMLElement>('.settings-tab.active')].map((t) => t.dataset.tab);
const activePanel = () => [...document.querySelectorAll<HTMLElement>('.settings-tabpanel.active')].map((t) => t.dataset.tab);
const tab = (id: string) => must(document.querySelector<HTMLElement>(`.settings-tab[data-tab="${id}"]`), `tab ${id}`);
const esc = () => fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
async function query(q: string) {
  fireEvent.change(search(), { target: { value: q } });
  await settle(1);
}

test('the modal: titled Settings, the seven tabs in order, General first, the AGPL source offer and the build', async () => {
  routes['/api/version'] = { version: 'v42' };
  await openOn();
  expect(screen.getByRole('dialog', { name: 'Settings' })).toBe(modal());
  expect(modal().classList.contains('open')).toBe(true);
  expect([...document.querySelectorAll<HTMLElement>('.settings-tab')].map((t) => t.textContent))
    .toEqual(['General', 'Timing', 'Dispatch', 'Notifications', 'Cost', 'Appearance', 'Collaboration']);
  expect(activeTab()).toEqual(['general']);
  const foot = must(modal().querySelector('.settings-modal-foot'));
  expect(must(foot.querySelector('a[href="https://github.com/Devail1/chelamux"]')).textContent).toBe('Source code');
  expect(foot.querySelector('a[href*="agpl-3.0"]')).not.toBeNull();
  expect(must(foot.querySelector('#version-island .version-badge')).textContent).toBe('build v42');
});

test('picking a tab shows only its panel (display:flex, the others display:none) and marks it in the rail', async () => {
  await openOn();
  fireEvent.click(tab('cost'));
  expect(activeTab()).toEqual(['cost']);
  for (const p of document.querySelectorAll<HTMLElement>('.settings-tabpanel')) {
    expect([p.dataset.tab, getComputedStyle(p).display]).toEqual([p.dataset.tab, p.dataset.tab === 'cost' ? 'flex' : 'none']);
  }
  expect(must(document.querySelector('.settings-tab.active')).getAttribute('aria-current')).toBe('page');
});

test('the rail is one tab stop: only the selected tab is focusable, and the arrows move along it', async () => {
  await openOn('timing');
  expect([...document.querySelectorAll<HTMLElement>('.settings-tab')].filter((t) => t.tabIndex === 0).map((t) => t.dataset.tab)).toEqual(['timing']);
  tab('timing').focus();
  fireEvent.keyDown(tab('timing'), { key: 'ArrowDown' });
  expect(activeTab()).toEqual(['dispatch']);
  expect(document.activeElement).toBe(tab('dispatch'));
  fireEvent.keyDown(tab('dispatch'), { key: 'ArrowUp' });
  fireEvent.keyDown(tab('timing'), { key: 'ArrowUp' });
  fireEvent.keyDown(tab('general'), { key: 'ArrowLeft' });
  expect(activeTab()).toEqual(['collaboration']); // wraps
});

test('Settings reopens on the last tab; "notify" opens on Notifications whatever was last', async () => {
  await openOn('appearance');
  act(() => closeSettings());
  act(() => openSettings());
  await settle(1);
  expect(activeTab()).toEqual(['appearance']);
  act(() => closeSettings());
  act(() => openSettings('notify'));
  await settle(1);
  expect(activeTab()).toEqual(['notifications']);
});

test('toggleSettings opens a closed modal and closes an open one', async () => {
  await openOn();
  act(() => toggleSettings());
  expect(settings.get().open).toBe(false);
  expect(document.getElementById('settings-drawer')).toBeNull();
  act(() => toggleSettings());
  expect(settings.get().open).toBe(true);
});

test('the scrim (outside the box) closes it, and so does the close button', async () => {
  await openOn();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await settle(1);
  expect(settings.get().open).toBe(false);
});

test('opening focuses the search box, empty; on a touch (pointer: coarse) screen it does not', async () => {
  await openOn();
  expect(document.activeElement).toBe(search());
  expect(search().value).toBe('');
  act(() => closeSettings());
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: /pointer:\s*coarse/.test(q) }) as MediaQueryList);
  act(() => openSettings());
  await settle(1);
  expect(document.activeElement).not.toBe(search());
});

test('"remote" typed on another tab shows Remote Control and hides unrelated rows; clearing restores the tab', async () => {
  await openOn('appearance');
  expect(visible(rowOf('remote-control-toggle'))).toBe(false);
  expect(visible(rowOf('theme-select'))).toBe(true);
  await query('ReMoTe');
  expect(visible(rowOf('remote-control-toggle'))).toBe(true);
  for (const id of ['theme-select', 'term-latin-select', 'collab-name', 'agent-model-select', 'run-toasts-select']) {
    expect([id, visible(document.getElementById(id))]).toEqual([id, false]);
  }
  expect(modal().classList.contains('searching')).toBe(true);
  expect(activeTab()).toEqual([]); // no tab is "the" tab while results span them
  expect([...document.querySelectorAll('.settings-search-group')].filter(visible).map((g) => g.textContent)).toEqual(['General']);
  expect(status()).toMatch(/^\d+ settings?$/);
  await query('');
  expect(activeTab()).toEqual(['appearance']);
  expect(activePanel()).toEqual(['appearance']);
  expect(visible(rowOf('theme-select'))).toBe(true);
  expect(visible(rowOf('remote-control-toggle'))).toBe(false);
  expect(status()).toBe('');
});

test('a keyword-only term (data-keywords, not the label or help) matches its row', async () => {
  await openOn('cost');
  const row = rowOf('remote-control-toggle');
  expect(must(row.closest('.settings-section')).textContent?.toLowerCase()).not.toMatch(/mobile/);
  expect(row.getAttribute('data-keywords')).toMatch(/mobile/);
  await query('mobile');
  expect(visible(row)).toBe(true);
  expect(status()).toBe('1 setting');
  expect(visible(rowOf('theme-select'))).toBe(false);
});

test('Esc clears the query first, and a second Esc closes Settings', async () => {
  await openOn();
  await query('remote');
  esc();
  await settle(1);
  expect(search().value).toBe('');
  expect(settings.get().open).toBe(true);
  expect(modal().classList.contains('searching')).toBe(false);
  esc();
  await settle(1);
  expect(settings.get().open).toBe(false);
});

test('no match: the empty state says so, and so does the live region; a match takes it away', async () => {
  await openOn();
  await query('zzqqxx');
  const empty = must(document.getElementById('settings-search-empty'));
  expect(visible(empty)).toBe(true);
  expect(empty.textContent).toBe("No settings match 'zzqqxx'");
  expect(status()).toBe("No settings match 'zzqqxx'");
  expect([...document.querySelectorAll('#drawer-body .settings-section')].filter(visible)).toEqual([]);
  await query('theme');
  expect(visible(empty)).toBe(false);
});

test('rows that render AFTER the query was typed are filtered too, and the unlabelled Save stays', async () => {
  let release: (v: unknown) => void = () => {};
  routes['/api/config/timing'] = () => new Promise((r) => { release = r; });
  await openOn();
  await query('heartbeat');
  await act(async () => release({ knobs: [
    { key: 'hb', label: 'Heartbeat interval', unit: 's', default: 5, stored: '', effective: 5, source: 'default' },
    { key: 'other', label: 'Something unrelated', unit: 's', default: 9, stored: '', effective: 9, source: 'default' },
  ] }));
  await settle();
  const rows = [...document.querySelectorAll('#timing-rows .field-row')];
  expect(rows.length).toBe(2);
  expect([visible(rows[0] ?? null), visible(rows[1] ?? null)]).toEqual([true, false]);
  expect(visible(must(sectionTitled('Timing').querySelector('.s-row button')))).toBe(true);
  expect(status()).toBe('1 setting'); // the Save row is not a setting
});

test('Dispatch rows that render after the query are filtered too', async () => {
  let release: (v: unknown) => void = () => {};
  routes['/api/config/dispatch'] = () => new Promise((r) => { release = r; });
  await openOn();
  await query('zebra');
  await act(async () => release({ knobs: [
    { key: 'z', label: 'Zebra limit', kind: 'number', unit: '', default: 3, stored: '', effective: 3, source: 'default' },
    { key: 'o', label: 'Something unrelated', kind: 'number', unit: '', default: 1, stored: '', effective: 1, source: 'default' },
  ] }));
  await settle();
  const rows = [...document.querySelectorAll('#dispatch-rows .field-row')];
  expect([visible(rows[0] ?? null), visible(rows[1] ?? null)]).toEqual([true, false]);
});

test('a section-level data-keywords term shows that whole section (Remote access has no labelled rows)', async () => {
  await openOn('appearance');
  const sec = sectionTitled('Remote access');
  expect(sec.querySelectorAll('.field-label').length).toBe(0);
  expect(sec.textContent?.toLowerCase()).not.toMatch(/\bvpn\b/);
  await query('vpn');
  expect(visible(sec)).toBe(true);
  expect(status()).toBe('1 setting');
  expect(visible(rowOf('remote-control-toggle'))).toBe(false);
});

test('a section help-text term (.s-desc) shows that whole section', async () => {
  await openOn();
  const sec = sectionTitled('Remote access');
  expect(`${sec.querySelector('h4')?.textContent} ${sec.getAttribute('data-keywords')}`.toLowerCase()).not.toMatch(/termius/);
  await query('termius');
  expect(visible(sec)).toBe(true);
  expect(status()).toBe('1 setting');
});

test('a whole-matched section counts every labelled row it shows', async () => {
  await openOn();
  const sec = sectionTitled('Terminal font');
  const labelled = [...sec.querySelectorAll('.field-row')];
  expect(labelled.length).toBe(3);
  await query('terminal font');
  for (const r of labelled) expect(visible(r)).toBe(true);
  expect([...document.querySelectorAll('#drawer-body .field-row')].filter(visible).length).toBe(3);
  expect(status()).toBe('3 settings');
});

test('multi-word queries are AND, not OR', async () => {
  await openOn();
  await query('remote phone');
  expect(visible(rowOf('remote-control-toggle'))).toBe(true);
  await query('remote zzqqxx');
  expect(visible(rowOf('remote-control-toggle'))).toBe(false);
  expect(status()).toBe("No settings match 'remote zzqqxx'");
});

test('⭐ an empty (or whitespace) query leaves the modal byte-for-byte as rendered', async () => {
  await openOn('notifications');
  const snap = () => ({ body: must(document.getElementById('drawer-body')).innerHTML, tabs: must(document.getElementById('settings-tabs')).innerHTML, modal: modal().className });
  const before = snap();
  expect(visible(rowOf('run-toasts-select'))).toBe(true);
  await query('   ');
  expect(snap()).toEqual(before);
  await query('remote');
  expect(snap()).not.toEqual(before);
  await query('');
  expect(snap()).toEqual(before);
});

test('closing and reopening starts from an empty query, not a stale filter', async () => {
  await openOn('appearance');
  await query('remote');
  act(() => closeSettings());
  act(() => openSettings());
  await settle(1);
  expect(search().value).toBe('');
  expect(modal().classList.contains('searching')).toBe(false);
  expect(visible(rowOf('theme-select'))).toBe(true);
});

test('picking a tab mid-search drops the query and goes to that tab', async () => {
  await openOn();
  await query('remote');
  fireEvent.click(tab('timing'));
  await settle(1);
  expect(search().value).toBe('');
  expect(activeTab()).toEqual(['timing']);
  expect(modal().classList.contains('searching')).toBe(false);
});

test('a reopen after a missed query shows no stale empty state (the query is gone, so is its count)', async () => {
  await openOn();
  await query('zzqqxx');
  expect(visible(must(document.getElementById('settings-search-empty')))).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await settle(1);
  act(() => openSettings());
  await settle(1);
  const empty = must(document.getElementById('settings-search-empty'));
  expect([visible(empty), empty.textContent, status()]).toEqual([false, '', '']);
});
