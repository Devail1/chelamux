import { afterEach, expect, test, vi } from 'vitest';

// The module reads the boot block and the base path once, at import: each test sets the page
// up first, then imports a fresh copy.
async function load(block: string | null, path = '/') {
  document.body.innerHTML = block === null ? '' : `<script type="application/json" id="chela-boot">${block}</script>`;
  window.history.replaceState({}, '', path);
  vi.resetModules();
  return import('../index.ts');
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
});

test('the boot block is read as rendered', async () => {
  const { BOOT, ASSET_VERSION } = await load(
    '{"assetVersion": "abc123", "terminalsEnabled": false, "wallTileDispatched": true, "sharingConfigured": false, "telegramConfigured": false, "dispatchOffByChoice": true}',
  );
  expect(BOOT).toEqual({ assetVersion: 'abc123', terminalsEnabled: false, wallTileDispatched: true, sharingConfigured: false, telegramConfigured: false, dispatchOffByChoice: true, identity: null });
  expect(ASSET_VERSION).toBe('abc123');
});

test('no block, or one that does not parse, runs as the legacy defaults', async () => {
  // CMX-387: a page with no block keeps today's UI, Share and Telegram controls included.
  // CMX-388: and the dispatcher's surfaces (dispatch is not off by choice).
  const defaults = { assetVersion: '', terminalsEnabled: true, wallTileDispatched: false, sharingConfigured: true, telegramConfigured: true, dispatchOffByChoice: false, identity: null };
  expect((await load(null)).BOOT).toEqual(defaults);
  expect((await load('{"terminalsEnabled": fal')).BOOT).toEqual(defaults);
  expect((await load('null')).BOOT).toEqual(defaults);
  expect((await load('"a string"')).BOOT).toEqual(defaults);
});

test('a field of the wrong type falls back alone; the others are kept', async () => {
  const { readBoot } = await load('{}');
  const page = (json: string) => {
    const doc = document.implementation.createHTMLDocument();
    doc.body.innerHTML = `<script type="application/json" id="chela-boot">${json}</script>`;
    return readBoot(doc);
  };
  expect(page('{"assetVersion": 7, "terminalsEnabled": false, "wallTileDispatched": true, "sharingConfigured": false, "telegramConfigured": 0, "dispatchOffByChoice": true}')).toEqual({
    assetVersion: '', terminalsEnabled: false, wallTileDispatched: true, sharingConfigured: false, telegramConfigured: true, dispatchOffByChoice: true, identity: null,
  });
  // CMX-388: a string "off" is not the boolean the server renders: dispatch stays ON (today's UI).
  expect(page('{"assetVersion": "v1", "terminalsEnabled": "false", "wallTileDispatched": 1, "sharingConfigured": "false", "telegramConfigured": false, "dispatchOffByChoice": "off"}')).toEqual({
    assetVersion: 'v1', terminalsEnabled: true, wallTileDispatched: false, sharingConfigured: true, telegramConfigured: false, dispatchOffByChoice: false, identity: null,
  });
});

test('identity (CMX-385): a login is kept; a missing or malformed one reads as null', async () => {
  const { readBoot } = await load('{}');
  const page = (json: string) => {
    const doc = document.implementation.createHTMLDocument();
    doc.body.innerHTML = `<script type="application/json" id="chela-boot">${json}</script>`;
    return readBoot(doc).identity;
  };
  expect(page('{"identity": {"login": "op@example.com", "name": "Op", "mode": "tailscale", "verified": false}}'))
    .toEqual({ login: 'op@example.com', name: 'Op', mode: 'tailscale', verified: false });
  expect(page('{"identity": {"login": "op@example.com", "mode": "cloudflare-access", "verified": true}}'))
    .toEqual({ login: 'op@example.com', name: null, mode: 'cloudflare-access', verified: true });
  expect(page('{"identity": {"login": "op@example.com", "mode": "tailscale", "verified": "yes"}}')?.verified).toBe(false);
  expect(page('{"identity": {"login": "op@example.com", "name": "", "mode": "tailscale"}}')?.name).toBeNull();
  expect(page('{"identity": {"login": "op@example.com", "name": 7, "mode": "tailscale"}}')?.name).toBeNull();
  expect(page('{"identity": null}')).toBeNull();
  expect(page('{"identity": {"login": "", "mode": "tailscale"}}')).toBeNull();
  expect(page('{"identity": {"login": 7, "mode": "tailscale"}}')).toBeNull();
  expect(page('{"identity": {"login": "op@example.com"}}')).toBeNull();
});

test('staticUrl carries the deploy version and the base path; without a version it stays plain', async () => {
  const versioned = await load('{"assetVersion": "f00d"}', '/chela/');
  expect(versioned.BASE_PATH).toBe('/chela');
  expect(versioned.staticUrl('collab/presence-owner.js')).toBe('/chela/static/v/f00d/collab/presence-owner.js');
  const plain = await load(null);
  expect(plain.BASE_PATH).toBe('');
  expect(plain.staticUrl('x.js')).toBe('/static/x.js');
});

test('api fetches under the base path with the caller\'s options and resolves to the JSON body', async () => {
  const { api } = await load(null, '/chela/');
  const fetch = vi.fn(() => Promise.resolve({ json: () => Promise.resolve({ ok: true }) }));
  vi.stubGlobal('fetch', fetch);
  const opts = { method: 'POST', body: '{}' };
  await expect(api('/api/agents/spawn', opts)).resolves.toEqual({ ok: true });
  expect(fetch.mock.calls).toEqual([['/chela/api/agents/spawn', opts]]);
});

test('the session token is read from the boot block and rides in the decide headers (CMX-392)', async () => {
  const mod = await load('{"assetVersion": "v", "sessionToken": "tok-123"}');
  expect(mod.SESSION_TOKEN).toBe('tok-123');
  expect(mod.decideHeaders()).toEqual({ 'Content-Type': 'application/json', 'X-Chela-Session-Token': 'tok-123' });
  // absent, unparsable or the wrong type: '' (the server then refuses, loudly), never a guess
  expect((await load(null)).SESSION_TOKEN).toBe('');
  expect((await load('{"sessionToken": 7}')).SESSION_TOKEN).toBe('');
  expect((await load('{"sessionToken": "x"')).SESSION_TOKEN).toBe('');
});
