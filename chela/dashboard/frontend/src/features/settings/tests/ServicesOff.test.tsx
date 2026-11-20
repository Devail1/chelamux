// CMX-387: with no relay (CHELA_COLLAB_RELAY unset) the Collaboration tab is one line saying
// how to turn sharing on, and with no Telegram bridge configured Telegram topics is one line
// pointing at docs/CONFIG.md. With either configured, the tab is exactly what it was.
// The menus' and the palette's Share rows are held in their own suites (PrimaryMenu, PaneHead,
// Palette), on the same two BOOT flags.
import { afterEach, expect, test, vi } from 'vitest';
import { BOOT } from '../../../shared/api/index.ts';
import { must, openOn, routes } from './setup.tsx';

// 🔐 CMX-447: seed the page's session token before the modules under test read it at import.
// Unseeded it is '', and an assertion against '' passes for a request that sends an EMPTY
// token, which the server refuses (docs/defeat_shapes/392).
vi.hoisted(() => {
  document.body.innerHTML = '<script type="application/json" id="chela-boot">{"sessionToken": "tok-settings"}</script>';
});

const SHARE_CONTROLS = ['collab-name', 'collab-relay', 'share-typing-toggle'];
const TELEGRAM_CONTROLS = ['tg-delete-toggle', 'tg-cleanup-btn', 'tg-topics-msg'];
const present = (ids: string[]) => ids.filter((id) => document.getElementById(id) !== null);
const flat = (id: string) => must(document.getElementById(id), `#${id}`).textContent?.replace(/\s+/g, ' ').trim();

const was = { ...BOOT };
afterEach(() => {
  BOOT.sharingConfigured = was.sharingConfigured;
  BOOT.telegramConfigured = was.telegramConfigured;
});

async function openWith(sharing: boolean, telegram: boolean) {
  BOOT.sharingConfigured = sharing;
  BOOT.telegramConfigured = telegram;
  routes['GET /api/config'] = { collab_relay: sharing ? 'wss://relay.example' : '', telegram_archived_topics: 2 };
  await openOn();
}

test('both off: no share or Telegram control renders, and each explanation line does', async () => {
  await openWith(false, false);
  expect(present(SHARE_CONTROLS)).toEqual([]);
  expect(present(TELEGRAM_CONTROLS)).toEqual([]);
  expect(flat('sharing-off-note')).toBe('Sharing is off: set CHELA_COLLAB_RELAY to enable (see docs/SHARE_SANDBOX.md).');
  expect(must(document.getElementById('sharing-off-note')).closest('.settings-tabpanel')?.getAttribute('data-tab')).toBe('collaboration');
  expect(flat('telegram-off-note')).toBe(
    'Telegram is off: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable (see the Telegram part of docs/CONFIG.md, under Secrets).');
  expect(must(document.getElementById('telegram-off-note')).closest('.settings-tabpanel')?.getAttribute('data-tab')).toBe('general');
});

test('both on: every control renders as before, and neither explanation line does', async () => {
  await openWith(true, true);
  expect(present(SHARE_CONTROLS)).toEqual(SHARE_CONTROLS);
  expect(present(TELEGRAM_CONTROLS)).toEqual(TELEGRAM_CONTROLS);
  expect(present(['sharing-off-note', 'telegram-off-note'])).toEqual([]);
  expect(flat('collab-relay')).toBe('wss://relay.example');
  expect(flat('tg-cleanup-btn')).toBe('Delete archived topics (2)');
});

test.each([
  [true, false, SHARE_CONTROLS, [], ['telegram-off-note']],
  [false, true, [], TELEGRAM_CONTROLS, ['sharing-off-note']],
])('sharing=%s telegram=%s: each flag gates only its own section', async (sharing, telegram, share, tg, notes) => {
  await openWith(sharing, telegram);
  expect(present(SHARE_CONTROLS)).toEqual(share);
  expect(present(TELEGRAM_CONTROLS)).toEqual(tg);
  expect(present(['sharing-off-note', 'telegram-off-note'])).toEqual(notes);
});
