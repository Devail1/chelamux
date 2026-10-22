// CMX-241 (CMX-212 P26): a Wall pane's header. Ported from the header halves of
// judge_battery_badge, orchestrator_ui, share_web_chip, wall_proxy_status_title,
// share_dialog (the Share row and the banner) and wallnav (labels, the menu rows, the window
// controls, kill) .test.mjs, plus Liav's 10-19 ask: a plain shell reads `Shell · <cwd>`.
import { act, fireEvent, render } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { pinnedPanes } from '../../../shared/store/wall.ts';
import { noteShare, shares, type ShareEntry } from '../../share/index.ts';
import { PaneHead, type HeadAgent, type PaneHeadHost, type PaneHeadProps } from '../index.ts';
import { must, openMenu, settle, stubFetch, viewport } from './setup.ts';

function spyHost() {
  return {
    minimize: vi.fn<PaneHeadHost['minimize']>(),
    dropPane: vi.fn<PaneHeadHost['dropPane']>(),
    wireDragStart: vi.fn<PaneHeadHost['wireDragStart']>(),
    leaveRoom: vi.fn<PaneHeadHost['leaveRoom']>(),
    labelsChanged: vi.fn<PaneHeadHost['labelsChanged']>(),
  };
}

const SESSION: HeadAgent = { window_id: '@1', name: 'liavedunix-2', claude_running: true, session_status: 'idle' };

/** One header inside a tile, as terminals.js builds it. */
function head(over: Partial<PaneHeadProps> = {}) {
  const host = spyHost();
  const props: PaneHeadProps = { wid: '@1', wall: true, agent: SESSION, orchWid: null, share: undefined, pinned: false, now: 0, host, ...over };
  const tile = document.createElement('div');
  tile.className = 'grid-stack-item-content';
  document.body.append(tile);
  const r = render(<PaneHead {...props} />, { container: tile });
  const q = (sel: string) => tile.querySelector<HTMLElement>(sel);
  return { ...r, tile, host, q, props, again: (o: Partial<PaneHeadProps>) => r.rerender(<PaneHead {...props} {...o} />) };
}

const text = (el: Element | null) => (el?.textContent ?? '').trim();

// --- a plain shell reads as a shell until Claude starts (Liav, 10-19) ----------------------

const SHELL: HeadAgent = { window_id: '@1', name: 'liavedunix-2', claude_running: false, shell_cwd: '~' };

test('a shell pane reads `Shell` with its cwd muted and a `shell` chip, never its window name', () => {
  const h = head({ agent: SHELL });
  expect(text(h.q('.pane-title'))).toBe('Shell');
  const sub = must(h.q('.pane-subtitle'));
  expect(text(sub)).toBe('~');
  expect(sub.classList.contains('pane-cwd')).toBe(true);
  const pill = must(h.q('.gs-state'));
  expect(text(pill)).toBe('shell');
  expect(pill.className).toBe('gs-state gs-state-shell');
  expect(pill.getAttribute('title')).toBe('Shell: no Claude session runs in this pane');
  // negative control: the label must not come from the window name
  expect(text(h.q('.gs-head'))).not.toContain('liavedunix-2');
});

test('the header switches to the session name and state the moment Claude starts in the pane', () => {
  const h = head({ agent: SHELL });
  expect(text(h.q('.pane-title'))).toBe('Shell');
  h.again({ agent: { ...SHELL, claude_running: true, shell_cwd: null, session_name: 'fix-the-bug', session_status: 'busy' } });
  expect(text(h.q('.pane-title'))).toBe('fix-the-bug');
  expect(text(h.q('.pane-subtitle'))).toBe('liavedunix-2');
  expect(h.q('.pane-subtitle')?.classList.contains('pane-cwd')).toBe(false);
  expect(text(h.q('.gs-state'))).toBe('working');
});

test('a running Claude with no title yet shows the window name and `idle`, as before', () => {
  const h = head({ agent: SESSION });
  expect(text(h.q('.pane-title'))).toBe('liavedunix-2');
  expect(h.q('.pane-subtitle')).toBeNull();
  expect(text(h.q('.gs-state'))).toBe('idle');
});

test('a row that does not say whether Claude runs is not called a shell', () => {
  const unsaid: HeadAgent = { ...SHELL };
  delete unsaid.claude_running;
  const h = head({ agent: unsaid });
  expect(text(h.q('.pane-title'))).toBe('liavedunix-2');
  expect(text(h.q('.gs-state'))).toBe('idle');
});

test('a shell a human named keeps that name, with its cwd beside it', () => {
  const h = head({ agent: { ...SHELL, name: 'build-box', manual_name: true, shell_cwd: '~/src' } });
  expect(text(h.q('.pane-title'))).toBe('build-box');
  expect(text(h.q('.pane-subtitle'))).toBe('~/src');
  expect(text(h.q('.gs-state'))).toBe('shell');
});

test('a shell whose cwd is unknown shows `Shell` alone, no empty subtitle', () => {
  const h = head({ agent: { ...SHELL, shell_cwd: null } });
  expect(text(h.q('.pane-title'))).toBe('Shell');
  expect(h.q('.pane-subtitle')).toBeNull();
});

test('a shell that wants a human still says so: `needs you` outranks `shell`', () => {
  const h = head({ agent: { ...SHELL, needs_human: true } });
  expect(text(h.q('.gs-state'))).toBe('needs you');
});

test('the single pane (a phone) labels a shell the same, and draws no state pill', () => {
  const h = head({ agent: SHELL, wall: false });
  expect(text(h.q('.pane-title'))).toBe('Shell');
  expect(h.q('.gs-label')).not.toBeNull();
  expect(h.q('.gs-grip')).toBeNull();
  expect(h.q('.gs-state')).toBeNull();
});

// --- labels (CMX-62, CMX-146) -------------------------------------------------------------

test('a manual name beats Claude\'s title, which beats the window name; the subtitle takes what is left', () => {
  const titled = { ...SESSION, ai_title: 'Refactor the flux capacitor' };
  const h = head({ agent: titled });
  expect(text(h.q('.pane-title'))).toBe('Refactor the flux capacitor');
  expect(text(h.q('.pane-subtitle'))).toBe('liavedunix-2');
  h.again({ agent: { ...titled, name: 'mine', manual_name: true } });
  expect(text(h.q('.pane-title'))).toBe('mine');
  expect(text(h.q('.pane-subtitle'))).toBe('Refactor the flux capacitor');
  expect(h.q('.pane-subtitle')?.getAttribute('title')).toBe('Refactor the flux capacitor');
});

test('a window the poll has not listed yet reads as its id', () => {
  const h = head({ agent: undefined });
  expect(text(h.q('.pane-title'))).toBe('@1');
});

test('on the wall the title is the drag handle (.gs-grip, "drag to move")', () => {
  const h = head();
  expect(h.q('.gs-grip .pane-title')).not.toBeNull();
  expect(h.q('.gs-grip')?.getAttribute('title')).toBe('drag to move');
});

// --- the state pill and the status dot (CMX-40, CMX-67, CMX-377, CMX-436) ---------------

const DOTS = ['working', 'waiting', 'idle', 'done'];
const dotOf = (el: Element | null) => DOTS.filter((c) => el?.classList.contains(c));

test.each([
  ['a running judge battery', { judge_battery: { state: 'testing', label: 'testing 3/6' } }, 'testing 3/6', 'testing', 'working'],
  ['a battery that died', { judge_battery: { state: 'died', label: 'judge run died' } }, 'judge run died', 'died', 'waiting'],
  ['a confirmed open PR', { pr: { url: 'https://x/pr/7', number: 7, state: 'open', draft: false } }, 'PR open', 'pr-open', 'done'],
  ['busy', { session_status: 'busy' }, 'working', 'working', 'working'],
  ['waiting on a reply', { session_status: 'waiting' }, 'needs you', 'needs-you', 'waiting'],
  ['a status chela could not read', { session_status: null }, 'unknown', 'unknown', 'idle'],
  ['idle', {}, 'idle', 'idle', 'idle'],
] as const)('%s: the pill reads its word and class, the dot its shape', (_, over, word, cls, dot) => {
  const h = head({ agent: { ...SESSION, ...over } as HeadAgent });
  expect(text(h.q('.gs-state-word'))).toBe(word);
  expect(h.q('.gs-state')?.className).toBe(`gs-state gs-state-${cls}`);
  const d = must(h.q('.gs-dot'));
  expect(d.classList.contains('term-status-dot')).toBe(true);
  expect(dotOf(d)).toEqual([dot]);
  expect(d.getAttribute('data-status-for')).toBe('@1');
});

test('the pill and the dot repaint their word on a live change, not just a class', () => {
  const h = head();
  h.again({ agent: { ...SESSION, session_status: 'busy' } });
  expect(text(h.q('.gs-state-word'))).toBe('working');
  expect(dotOf(h.q('.gs-dot'))).toEqual(['working']);
  h.again({ agent: { ...SESSION, session_status: 'idle' } });
  expect(text(h.q('.gs-state-word'))).toBe('idle');
});

test('a sandboxed session\'s state says it came from the proxy, on the pill and the dot', () => {
  const h = head({ agent: { ...SESSION, session_status: 'busy', status_source: 'sandbox-proxy' } });
  expect(h.q('.gs-state')?.getAttribute('title')).toBe('Working (from the sandbox proxy)');
  expect(h.q('.gs-dot')?.getAttribute('title')).toBe('Working (from the sandbox proxy)');
  h.again({ agent: { ...SESSION, session_status: 'busy' } });
  expect(h.q('.gs-state')?.getAttribute('title')).toBe('Working');
});

test('a battery\'s running experiment is never printed on the pane', () => {
  const h = head({ agent: { ...SESSION, judge_battery: { state: 'testing', label: 'testing 1/2', current: 'guard.py: SECRET' } as HeadAgent['judge_battery'] } });
  expect(h.tile.innerHTML).not.toContain('SECRET');
});

// --- the orchestrator ring and its menu row (CMX-117 E, CMX-32, CMX-139) ------------------

test('the ring is on the dot\'s wrapper iff this pane holds the decisions inbox', () => {
  const h = head({ orchWid: '@1' });
  const ring = must(h.q('.gs-dot-ring'));
  expect(ring.classList.contains('gs-dot-orch')).toBe(true);
  expect(ring.querySelector('.gs-dot')).not.toBeNull();
  expect(must(h.q('.gs-dot')).classList.contains('gs-dot-orch')).toBe(false);
  h.again({ orchWid: '@2' });
  expect(ring.classList.contains('gs-dot-orch')).toBe(false);
});

test('the Orchestrator row is checked in lockstep with the ring, and its pick subscribes this pane', async () => {
  const calls = stubFetch(() => [200, { ok: true, wid: '@1', name: 'x', state: 'ok', why: '', queued: 0 }]);
  const h = head();
  const row = must(openMenu(h.tile).querySelector('.gs-orch-btn'));
  expect(row.getAttribute('aria-checked')).toBe('false');
  expect(row.getAttribute('title')).toBe('Click to receive the decisions inbox in this pane');
  expect(row.getAttribute('data-wid')).toBe('@1');
  fireEvent.click(row);
  await settle();
  expect(calls).toEqual([{ url: '/api/orchestrator/subscribe', method: 'POST', body: { wid: '@1' } }]);
});

test('on the owning pane the row reads checked and its pick releases the slot', async () => {
  const calls = stubFetch(() => [200, { ok: true, wid: null, name: null, state: 'unregistered', why: '', queued: 0 }]);
  const h = head({ orchWid: '@1' });
  const row = must(openMenu(h.tile).querySelector('.gs-orch-btn'));
  expect(row.getAttribute('aria-checked')).toBe('true');
  expect(row.classList.contains('on')).toBe(true);
  expect(row.getAttribute('title')).toBe('This pane receives the decisions inbox, click to release');
  fireEvent.click(row);
  await settle();
  expect(calls).toEqual([{ url: '/api/orchestrator/release', method: 'POST', body: { wid: '@1' } }]);
});

test('the row is disabled while a toggle is in flight, and comes back after a refusal', async () => {
  let release: (v: unknown) => void = () => {};
  vi.stubGlobal('fetch', () => new Promise((r) => (release = r)));
  const err = vi.spyOn(console, 'error').mockImplementation(() => {});
  const h = head();
  fireEvent.click(must(openMenu(h.tile).querySelector('.gs-orch-btn')));
  await settle();
  expect(must(openMenu(h.tile).querySelector('.gs-orch-btn')).hasAttribute('data-disabled')).toBe(true);
  release({ ok: true, json: () => Promise.resolve({ ok: false, error: 'dead window' }) });
  await settle();
  expect(err).toHaveBeenCalledWith('orchestrator toggle failed', 'dead window');
  expect(must(openMenu(h.tile).querySelector('.gs-orch-btn')).hasAttribute('data-disabled')).toBe(false);
});

// --- the Share row, the web chip, the UNSANDBOXED banner (CMX-403, CMX-418, CMX-421) ------

const entry = (mode: ShareEntry['mode'], expiresAt: number | null = null): ShareEntry => ({ mode, expiresAt });

test.each([
  ['view', 'View only', 'lucide-eye'],
  ['typing', 'Allow typing', 'lucide-keyboard'],
  ['unsandboxed', 'Full access: UNSANDBOXED', 'lucide-triangle-alert'],
] as const)('a %s share: the Share row carries its mode from the first render', (mode, label, glyph) => {
  const h = head({ share: entry(mode) });
  const row = must(openMenu(h.tile).querySelector('.gs-share-btn'));
  expect(row.classList.contains('on')).toBe(true);
  expect(row.getAttribute('data-mode')).toBe(mode);
  expect(row.getAttribute('data-wid')).toBe('@1');
  const g = must(row.querySelector('.gs-share-mode'));
  expect(g.getAttribute('title')).toBe(label);
  expect(g.querySelector(`svg.${glyph}`)?.getAttribute('aria-label')).toBe(label);
  expect(row.querySelector('.gs-share-count')?.hasAttribute('hidden')).toBe(true);
});

test('an unshared pane\'s Share row has no mode and no glyph', () => {
  const h = head();
  const row = must(openMenu(h.tile).querySelector('.gs-share-btn'));
  expect(row.classList.contains('on')).toBe(false);
  expect(row.getAttribute('data-mode')).toBe('');
  expect(row.querySelector('.gs-share-mode')).toBeNull();
  expect(row.querySelector('.gs-share-count')).not.toBeNull();
});

test('picking Share runs the share flow for THIS pane (it reads the pane\'s live share first)', async () => {
  const calls = stubFetch(() => [200, {}]);
  const h = head({ wid: '@4', agent: { ...SESSION, window_id: '@4' } });
  fireEvent.click(must(openMenu(h.tile).querySelector('.gs-share-btn')));
  await settle();
  expect(calls[0]?.url).toBe('/api/term/%404/share-info');
});

test('the web chip shows only on a window launched with web access', () => {
  const h = head({ agent: { ...SESSION, share_net: 'web' } });
  const chip = must(h.q('.gs-net-badge'));
  expect(chip.hidden).toBe(false);
  expect(text(chip)).toBe('web');
  expect(chip.getAttribute('data-net-for')).toBe('@1');
  h.again({ agent: { ...SESSION, share_net: 'none' } });
  expect(chip.hidden).toBe(true);
  h.again({ agent: { ...SESSION, share_net: null } });
  expect(chip.hidden).toBe(true);
});

test('the red banner shows exactly while an UNSANDBOXED override is live, with its time left', () => {
  const now = Date.UTC(2026, 9, 21, 12);
  const h = head({ share: entry('unsandboxed', now / 1000 + (12 * 1440 + 4 * 60) * 60 + 30), now });
  const banner = must(h.q('.gs-unsafe-banner'));
  expect(banner.hidden).toBe(false);
  expect(banner.getAttribute('role')).toBe('status');
  expect(text(banner)).toBe('UNSANDBOXED: guest can type · 12d 4h left');
  h.again({ share: entry('typing') });
  expect(banner.hidden).toBe(true);
  expect(text(banner)).toBe('');
  h.again({ share: undefined });
  expect(banner.hidden).toBe(true);
});

// --- the Wire and Pin rows, the room badge, the facepile slot -----------------------------

test('pressing Wire starts the drag from this pane and closes the menu at once', async () => {
  const h = head();
  const row = must(openMenu(h.tile).querySelector('.gs-port'));
  expect(row.getAttribute('title')).toBe('Wire this agent to another: drag onto a peer');
  fireEvent.mouseDown(row, { clientX: 7, clientY: 9, buttons: 1 });
  await settle();
  expect(h.host.wireDragStart).toHaveBeenCalledTimes(1);
  const [e, from, wid] = must(h.host.wireDragStart.mock.calls[0]);
  expect([e.type, e.clientX, e.clientY, from, wid]).toEqual(['mousedown', 7, 9, row, '@1']);
  expect(document.querySelector('.pane-overflow-menu')).toBeNull();
});

test('picking Pin pins this pane (persisted), and the row reads checked while pinned', () => {
  const h = head();
  const row = must(openMenu(h.tile).querySelector('.gs-pin-btn'));
  expect(row.getAttribute('aria-checked')).toBe('false');
  fireEvent.click(row);
  expect(pinnedPanes.get().has('@1')).toBe(true);
  expect(JSON.parse(localStorage.getItem('pc_wall_pinned') ?? '[]')).toEqual(['@1']);
  h.again({ pinned: true });
  const on = must(openMenu(h.tile).querySelector('.gs-pin-btn'));
  expect(on.getAttribute('aria-checked')).toBe('true');
  expect(on.classList.contains('on')).toBe(true);
  fireEvent.click(on);
  expect(pinnedPanes.get().has('@1')).toBe(false);
});

test('the wall menu lists Wire, Share, Orchestrator, Pin as labeled rows with icons', () => {
  const h = head();
  const rows = [...openMenu(h.tile).querySelectorAll('.popover-item.ov-item')];
  expect(rows.map((r) => text(r.querySelector('.ov-label')))).toEqual(['Wire to…', 'Share current session', 'Orchestrator', 'Pin']);
  for (const r of rows) expect(r.querySelector('svg')).not.toBeNull();
});

test('the single pane\'s menu has no Wire or Pin (nothing to wire to, nothing to reflow)', () => {
  const h = head({ wall: false });
  const rows = [...openMenu(h.tile).querySelectorAll('.ov-label')].map(text);
  expect(rows).toEqual(['Share current session', 'Orchestrator']);
  expect(h.q('.gs-room')).toBeNull();
  expect(h.q('.gs-min-btn')).toBeNull();
});

test('the room badge starts hidden and a click leaves the room it names (wire.ts fills it)', () => {
  const h = head();
  const badge = must(h.q('.gs-room'));
  expect(badge.hidden).toBe(true);
  expect(badge.querySelector('.gs-room-name')).not.toBeNull();
  fireEvent.click(badge);
  expect(h.host.leaveRoom).toHaveBeenCalledWith(badge, '@1');
});

test('what legacy writes into the facepile and room badge survives a repaint', () => {
  const h = head();
  const face = must(h.q('.gs-presence'));
  expect(face.getAttribute('data-presence-for')).toBe('@1');
  face.innerHTML = '<div class="gs-owner-fp"></div>';
  const badge = must(h.q('.gs-room'));
  badge.hidden = false;
  h.again({ agent: { ...SESSION, session_status: 'busy' } });
  expect(face.querySelector('.gs-owner-fp')).not.toBeNull();
  expect(badge.hidden).toBe(false);
});

// --- the window controls ------------------------------------------------------------------

test('minimize hands the pane to the dock', () => {
  const h = head();
  fireEvent.click(must(h.q('.gs-min-btn')));
  expect(h.host.minimize).toHaveBeenCalledWith('@1');
});

test('maximize lifts THIS pane over the page and the same button restores it', () => {
  const h = head();
  const btn = must(h.q('.gs-max-btn'));
  expect(btn.getAttribute('aria-pressed')).toBe('false');
  fireEvent.click(btn);
  expect(h.tile.classList.contains('pane-maximized')).toBe(true);
  expect(document.body.classList.contains('pane-is-maximized')).toBe(true);
  expect(btn.getAttribute('aria-pressed')).toBe('true');
  expect(btn.getAttribute('aria-label')).toBe('Restore pane');
  expect(btn.querySelector('svg.lucide-minimize-2')).not.toBeNull();
  fireEvent.click(btn);
  expect(h.tile.classList.contains('pane-maximized')).toBe(false);
  expect(document.body.classList.contains('pane-is-maximized')).toBe(false);
  expect(btn.getAttribute('aria-label')).toBe('Maximize pane');
});

test('maximize outside a pane does nothing', () => {
  const host = spyHost();
  const { container } = render(<PaneHead wid="@1" wall agent={SESSION} orchWid={null} share={undefined} pinned={false} now={0} host={host} />);
  fireEvent.click(must(container.querySelector('.gs-max-btn')));
  expect(document.body.classList.contains('pane-is-maximized')).toBe(false);
  expect(container.querySelector('.gs-max-btn')?.getAttribute('aria-pressed')).toBe('false');
});

test('on a phone the single pane gets Fill screen, which toggles the full-screen view', () => {
  viewport(true);
  const h = head({ wall: false });
  const btn = must(h.q('.gs-mobile-full-btn'));
  expect(btn.getAttribute('aria-label')).toBe('Fill screen');
  fireEvent.click(btn);
  expect(document.body.classList.contains('term-mobile-full')).toBe(true);
  expect(btn.getAttribute('aria-pressed')).toBe('true');
  expect(btn.getAttribute('aria-label')).toBe('Exit fullscreen');
  fireEvent.click(btn);
  expect(document.body.classList.contains('term-mobile-full')).toBe(false);
});

test('Fill screen exists only for the single pane on a phone', () => {
  viewport(true);
  expect(head({ wall: true }).q('.gs-mobile-full-btn')).toBeNull();
  viewport(false);
  expect(head({ wall: false }).q('.gs-mobile-full-btn')).toBeNull();
});

// --- kill, with its confirm ---------------------------------------------------------------

test('kill asks first, over the pane, and Cancel leaves it alone', () => {
  const h = head();
  const kill = must(h.q('.gs-kill-btn'));
  expect(kill.getAttribute('aria-label')).toBe('Kill this session');
  fireEvent.click(kill);
  const bar = must(h.q('.pane-kill-confirm'));
  expect(bar.parentElement).toBe(h.tile);
  expect(bar.getAttribute('data-agent')).toBe('@1');
  expect(text(bar.querySelector('.kanban-confirm-msg'))).toBe('Kill liavedunix-2?');
  expect(kill.style.visibility).toBe('hidden');
  fireEvent.click(must([...bar.querySelectorAll('button')].find((b) => text(b) === 'Cancel')));
  expect(h.q('.pane-kill-confirm')).toBeNull();
  expect(kill.style.visibility).toBe('');
});

test('a confirmed kill posts the window and drops its tile at once', async () => {
  const calls = stubFetch(() => [200, { ok: true }]);
  const h = head({ agent: SHELL });
  fireEvent.click(must(h.q('.gs-kill-btn')));
  expect(text(h.q('.kanban-confirm-msg'))).toBe('Kill Shell?');
  fireEvent.click(must(h.q('.pane-kill-confirm .btn-confirm')));
  expect(text(h.q('.kanban-confirm-msg'))).toBe('Killing…');
  await settle();
  expect(calls).toEqual([{ url: '/api/agents/kill', method: 'POST', body: { agent: '@1' } }]);
  expect(h.host.dropPane).toHaveBeenCalledWith('@1');
});

test.each([
  ['a refusal', [200, { ok: false, error: 'no such window' }], 'no such window'],
  ['an HTTP error with no reason', [500, {}], 'HTTP 500'],
  ['a body that is not JSON', [502, new Error('bad json')], 'HTTP 502'],
  ['an ok status that is not ok', [200, { ok: false }], 'HTTP 200'],
  ['an HTTP error whose body claims ok', [500, { ok: true }], 'HTTP 500'],
] as const)('%s keeps the tile and says why, and Close puts the button back', async (_, answer, msg) => {
  stubFetch(() => answer as [number, unknown]);
  const h = head();
  fireEvent.click(must(h.q('.gs-kill-btn')));
  fireEvent.click(must(h.q('.pane-kill-confirm .btn-confirm')));
  await settle();
  expect(h.host.dropPane).not.toHaveBeenCalled();
  const err = must(h.q('.pane-kill-confirm .pane-kill-error'));
  expect(text(err)).toBe(msg);
  fireEvent.click(must([...must(h.q('.pane-kill-confirm')).querySelectorAll('button')].find((b) => text(b) === 'Close')));
  expect(h.q('.pane-kill-confirm')).toBeNull();
  expect(must(h.q('.gs-kill-btn')).style.visibility).toBe('');
});

test('an unreachable dashboard keeps the tile and shows the error', async () => {
  stubFetch(() => {
    throw new Error('offline');
  });
  const h = head();
  fireEvent.click(must(h.q('.gs-kill-btn')));
  fireEvent.click(must(h.q('.pane-kill-confirm .btn-confirm')));
  await settle();
  expect(text(h.q('.pane-kill-error'))).toBe('Error: offline');
  expect(h.host.dropPane).not.toHaveBeenCalled();
});

// --- rename: the WINDOW, through the server --------------------------------------------------

function rename(h: ReturnType<typeof head>) {
  fireEvent.doubleClick(must(h.q('.pane-title')));
  return must(h.tile.querySelector<HTMLInputElement>('input.pane-title-edit'));
}

test('double-click edits the REAL window name; Enter renames the window and rereads the agents', async () => {
  const fresh = [{ ...SESSION, name: 'renamed' }];
  const calls = stubFetch((url) => [200, url.endsWith('/rename') ? { ok: true } : fresh]);
  const h = head({ agent: { ...SESSION, ai_title: 'A title' } });
  const input = rename(h);
  expect(input.value).toBe('liavedunix-2');
  expect(document.activeElement).toBe(input);
  fireEvent.change(input, { target: { value: '  renamed ' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await settle();
  expect(calls).toEqual([
    { url: '/api/agents/%401/rename', method: 'POST', body: { name: 'renamed' } },
    { url: '/api/agents', method: 'GET', body: undefined },
  ]);
  expect(agents.get()).toEqual(fresh);
  expect(h.host.labelsChanged).toHaveBeenCalledTimes(1);
  expect(h.q('input.pane-title-edit')).toBeNull();
  expect(h.q('.pane-title')).not.toBeNull();
});

test('leaving the field commits too, once', async () => {
  const calls = stubFetch((url) => [200, url.endsWith('/rename') ? { ok: true } : []]);
  const h = head();
  const input = rename(h);
  fireEvent.change(input, { target: { value: 'other' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  fireEvent.blur(input);
  await settle();
  expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
});

test.each([
  ['Escape', 'other', { key: 'Escape' }],
  ['an unchanged name', 'liavedunix-2', { key: 'Enter' }],
  ['an empty name', '   ', { key: 'Enter' }],
])('%s sends nothing', async (_, value, key) => {
  const calls = stubFetch(() => [200, {}]);
  const h = head();
  const input = rename(h);
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, key);
  await settle();
  expect(calls).toEqual([]);
  expect(text(h.q('.pane-title'))).toBe('liavedunix-2');
});

test('a refused rename says why on the pane and relabels nothing', async () => {
  const calls = stubFetch(() => [200, { ok: false, error: 'bad name' }]);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const h = head();
  const input = rename(h);
  fireEvent.change(input, { target: { value: 'x y' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await settle();
  expect(calls).toHaveLength(1);
  expect(text(h.q('.gs-head .term-share-toast'))).toBe('Rename failed: bad name');
  expect(h.host.labelsChanged).not.toHaveBeenCalled();
});

test('a rename whose agents reread is not a list leaves an empty list, never the error body', async () => {
  stubFetch((url) => [200, url.endsWith('/rename') ? { ok: true } : { error: 'boom' }]);
  agents.set([{ window_id: '@1', name: 'liavedunix-2' }]);
  const h = head();
  const input = rename(h);
  fireEvent.change(input, { target: { value: 'z' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await settle();
  expect(agents.get()).toEqual([]);
});

test('keys typed in the rename field never reach a page shortcut', () => {
  const h = head();
  const input = rename(h);
  const seen = vi.fn();
  document.addEventListener('keydown', seen);
  fireEvent.keyDown(input, { key: 'k', ctrlKey: true });
  document.removeEventListener('keydown', seen);
  expect(seen).not.toHaveBeenCalled();
});

// --- the share store reaches a header through its props --------------------------------

test('noteShare is what the page publishes; the header reads it through its share prop', () => {
  act(() => noteShare('@1', 'typing'));
  const h = head({ share: shares.get().get('@1') });
  expect(must(openMenu(h.tile).querySelector('.gs-share-btn')).getAttribute('data-mode')).toBe('typing');
});
