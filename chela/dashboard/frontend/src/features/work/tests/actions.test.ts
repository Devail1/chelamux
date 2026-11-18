// 🔐 CMX-392: a card action carries the page's session token. The server refuses the card's
// Merge (it runs `gh pr merge` itself) without it, so an agent on loopback cannot press it.
import { afterEach, expect, test, vi } from 'vitest';

// The page's token, seeded into the boot block BEFORE the module under test reads it at
// import: the test pins the VALUE (a header key with an empty or wrong token is refused).
const TOKEN = vi.hoisted(() => {
  const t = 'tok-work-actions-9f3c';
  document.body.innerHTML = `<script type="application/json" id="chela-boot">{"sessionToken": "${t}"}</script>`;
  return t;
});

vi.mock('../../../shared/store/work.ts', async (orig) => ({ ...(await orig<object>()), refreshWork: vi.fn() }));

afterEach(() => vi.unstubAllGlobals());

test('Merge posts to the run with the session-token header', async () => {
  const calls: { url: string; opts: RequestInit }[] = [];
  vi.stubGlobal('fetch', (url: string, opts: RequestInit) => {
    calls.push({ url, opts });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
  });
  const { mergeRun } = await import('../index.ts');
  expect(await mergeRun('CMX-1')).toBeNull();
  expect(calls[0]?.url).toBe('/api/dispatcher/runs/CMX-1/merge');
  const headers = calls[0]?.opts.headers as Record<string, string>;
  expect(headers['X-Chela-Session-Token']).toBe(TOKEN);
  expect(headers['Content-Type']).toBe('application/json');
});
