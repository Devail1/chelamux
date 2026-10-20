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
    '{"assetVersion": "abc123", "terminalsEnabled": false, "wallTileDispatched": true}',
  );
  expect(BOOT).toEqual({ assetVersion: 'abc123', terminalsEnabled: false, wallTileDispatched: true });
  expect(ASSET_VERSION).toBe('abc123');
});

test('no block, or one that does not parse, runs as the legacy defaults', async () => {
  const defaults = { assetVersion: '', terminalsEnabled: true, wallTileDispatched: false };
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
  expect(page('{"assetVersion": 7, "terminalsEnabled": false, "wallTileDispatched": true}')).toEqual({
    assetVersion: '', terminalsEnabled: false, wallTileDispatched: true,
  });
  expect(page('{"assetVersion": "v1", "terminalsEnabled": "false", "wallTileDispatched": 1}')).toEqual({
    assetVersion: 'v1', terminalsEnabled: true, wallTileDispatched: false,
  });
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
