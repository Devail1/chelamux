// CMX-242 (CMX-212 P32): the wire. Ported from the wire halves of wall and wallnav .test.mjs
// (the drag state, every way it ends, the origin), plus the drop, the leave and the accents.
import { act } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { termMode } from '../../../shared/store/wall.ts';
import { cancelWire, leaveRoom, refreshRooms, replayRoomAccents, startWire } from '../index.ts';

type Call = { url: string; method: string; body: unknown };
let calls: Call[] = [];
let answer: (url: string) => unknown = () => ({});

function stubFetch() {
  calls = [];
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
    try {
      const body = answer(url);
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    } catch (e) {
      return Promise.reject(e instanceof Error ? e : new Error(String(e)));
    }
  });
}

const tile = (wid: string) => `<div class="grid-stack-item" gs-id="${wid}"><div class="grid-stack-item-content">
  <div class="gs-head"><button class="gs-room" hidden><span class="gs-room-name"></span></button></div></div></div>`;

let hitWid: string | null = null;

beforeEach(() => {
  document.body.innerHTML = `<div id="term-stage"><div class="grid-stack">${tile('@1') + tile('@2')}</div></div>`;
  termMode.set('wall');
  agents.set([{ window_id: '@2', name: 'peer' }]);
  answer = () => ({});
  stubFetch();
  hitWid = null;
  document.elementFromPoint = () => (hitWid ? document.querySelector(`.grid-stack-item[gs-id="${hitWid}"] .gs-head`) : null);
});

afterEach(() => {
  cancelWire();
  vi.unstubAllGlobals();
});

const stage = () => document.getElementById('term-stage') as HTMLElement;
const grid = () => stage().querySelector('.grid-stack') as HTMLElement;
const item = (wid: string) => stage().querySelector(`.grid-stack-item[gs-id="${wid}"]`) as HTMLElement;
const down = (over: MouseEventInit = {}) => new MouseEvent('mousedown', { bubbles: true, cancelable: true, buttons: 1, clientX: 10, clientY: 10, ...over });
const live = () => grid().classList.contains('gs-dragging') || stage().classList.contains('wire-live') || !!stage().querySelector('.wire-overlay');
const settle = async () => { for (let i = 0; i < 4; i++) await act(() => new Promise((r) => setTimeout(r, 0))); };

function start(wid = '@1', e = down()) {
  const from = document.createElement('button');
  startWire(e, from, wid);
  return e;
}

test('a press starts the wire: the iframes stop eating the mouse, every tile sprouts a socket, the line draws', () => {
  const e = start();
  expect(e.defaultPrevented).toBe(true);   // gridstack must not take it as a tile drag
  expect(grid().classList.contains('gs-dragging')).toBe(true);
  expect(stage().classList.contains('wire-live')).toBe(true);
  expect(item('@1').classList.contains('wire-source')).toBe(true);
  expect(stage().querySelector('svg.wire-overlay path')?.getAttribute('d')).toMatch(/^M /);
});

test('the line leaves the source PANE\'s top-center, not the menu row it was started from', () => {
  stage().getBoundingClientRect = () => ({ left: 0, top: 0, right: 1000, bottom: 800, width: 1000, height: 800 }) as DOMRect;
  item('@1').getBoundingClientRect = () => ({ left: 100, top: 50, right: 400, bottom: 250, width: 300, height: 200 }) as DOMRect;
  const from = document.createElement('button');
  from.getBoundingClientRect = () => ({ left: 900, top: 700, right: 940, bottom: 720, width: 40, height: 20 }) as DOMRect;
  startWire(down(), from, '@1');
  expect(stage().querySelector('.wire-overlay path')?.getAttribute('d')).toMatch(/^M 250 50 C /);
});

test('moving follows the pointer and marks a peer under it as the target; the source is never one', () => {
  start();
  hitWid = '@2';
  document.dispatchEvent(new MouseEvent('mousemove', { buttons: 1, clientX: 300, clientY: 200 }));
  expect(item('@2').classList.contains('wire-target')).toBe(true);
  expect(stage().querySelector('.wire-overlay path')?.getAttribute('d')).toMatch(/300 200$/);
  hitWid = '@1';
  document.dispatchEvent(new MouseEvent('mousemove', { buttons: 1, clientX: 20, clientY: 20 }));
  expect(item('@2').classList.contains('wire-target')).toBe(false);
  expect(item('@1').classList.contains('wire-target')).toBe(false);
});

test.each([
  ['Escape', () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))],
  ['a window blur (released off the window)', () => window.dispatchEvent(new Event('blur'))],
  ['a cancelled pointer', () => document.dispatchEvent(new Event('pointercancel'))],
  ['a move with no button held', () => document.dispatchEvent(new MouseEvent('mousemove', { buttons: 0, clientX: 40, clientY: 40 }))],
])('%s ends the wire and gives the wall back', (_, end) => {
  start();
  hitWid = '@2';
  document.dispatchEvent(new MouseEvent('mousemove', { buttons: 1, clientX: 30, clientY: 30 }));
  end();
  expect(live()).toBe(false);
  expect(item('@1').classList.contains('wire-source')).toBe(false);
  expect(item('@2').classList.contains('wire-target')).toBe(false);
  expect(calls.filter((c) => c.method === 'POST')).toEqual([]);   // a cancel, never a room
});

test('another key leaves the wire alone', () => {
  start();
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
  expect(live()).toBe(true);
});

test('a drop on a peer puts the two in a room, says so, and repaints the accents', async () => {
  answer = (url) => (url === '/api/rooms' ? { rooms: { r1: { members: { '@1': {}, '@2': {} } } } } : { room: 'r1' });
  start();
  hitWid = '@2';
  document.dispatchEvent(new MouseEvent('mouseup', { clientX: 300, clientY: 200 }));
  expect(live()).toBe(false);
  await settle();
  expect(calls[0]).toEqual({ url: '/api/rooms/join', method: 'POST', body: { wids: ['@1', '@2'] } });
  expect(item('@1').querySelector('.term-share-toast')?.textContent).toBe('peer joined r1');
  expect(item('@1').querySelector('.grid-stack-item-content')?.classList.contains('in-room')).toBe(true);
  expect(item('@2').querySelector('.gs-room-name')?.textContent).toBe('r1');
});

test('a drop on the source itself, or on nothing, creates no room', async () => {
  for (const at of ['@1', null]) {
    start();
    hitWid = at;
    document.dispatchEvent(new MouseEvent('mouseup', {}));
    await settle();
  }
  expect(calls).toEqual([]);
});

test('a refused join says why; an unreachable dashboard says so', async () => {
  answer = () => ({ error: 'loop guard' });
  start();
  hitWid = '@2';
  document.dispatchEvent(new MouseEvent('mouseup', {}));
  await settle();
  expect(item('@1').querySelector('.term-share-toast')?.textContent).toBe('loop guard');
  answer = () => { throw new Error('down'); };
  start();
  hitWid = '@2';
  document.dispatchEvent(new MouseEvent('mouseup', {}));
  await settle();
  expect(item('@1').querySelector('.term-share-toast')?.textContent).toBe('wire failed, could not reach the dashboard');
});

test('outside wall mode a press does nothing (but is still consumed)', () => {
  termMode.set('single');
  const e = start();
  expect(e.defaultPrevented).toBe(true);
  expect(live()).toBe(false);
});

test('a second press replaces the wire in flight: never two at once', () => {
  start('@1');
  start('@2');
  expect(stage().querySelectorAll('.wire-overlay').length).toBe(1);
  expect(item('@1').classList.contains('wire-source')).toBe(false);
  expect(item('@2').classList.contains('wire-source')).toBe(true);
});

test('the room badge leaves the room it names, then repaints', async () => {
  answer = (url) => (url === '/api/rooms' ? { rooms: {} } : { ok: true });
  const badge = item('@2').querySelector('.gs-room') as HTMLElement;
  badge.setAttribute('data-room', 'r1');
  await leaveRoom(badge, '@2');
  expect(calls.map((c) => [c.url, c.method, c.body])).toEqual([
    ['/api/rooms/leave', 'POST', { wid: '@2', room: 'r1' }],
    ['/api/rooms', 'GET', undefined],
  ]);
  calls = [];
  badge.removeAttribute('data-room');
  await leaveRoom(badge, '@2');
  expect(calls).toEqual([]);
});

test('the rooms read patches the tiles; a fresh tile replays the last one; a failed read changes nothing', async () => {
  answer = () => ({ rooms: { r2: { members: { '@1': {} } } } });
  await refreshRooms();
  expect(item('@1').querySelector('.grid-stack-item-content')?.classList.contains('in-room')).toBe(true);
  grid().insertAdjacentHTML('beforeend', tile('@1').replace('gs-id="@1"', 'gs-id="@1" data-fresh="1"'));
  const fresh = grid().querySelector('[data-fresh] .grid-stack-item-content') as HTMLElement;
  expect(fresh.classList.contains('in-room')).toBe(false);
  replayRoomAccents();
  expect(fresh.classList.contains('in-room')).toBe(true);
  answer = () => { throw new Error('down'); };
  await refreshRooms();
  expect(fresh.classList.contains('in-room')).toBe(true);
});

test('outside wall mode the rooms are not read', async () => {
  termMode.set('single');
  await refreshRooms();
  expect(calls).toEqual([]);
});

test('a stage rewritten mid-wire (a new pane, a rebuild) still ends cleanly', () => {
  start();
  stage().innerHTML = `<div class="grid-stack">${tile('@1') + tile('@2')}</div>`;   // what buildWall does
  expect(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))).not.toThrow();
  expect(stage().classList.contains('wire-live')).toBe(false);
  start();   // and the next wire draws
  expect(stage().querySelectorAll('.wire-overlay').length).toBe(1);
});

test('the wire\'s container leaves with it', () => {
  start();
  expect(stage().querySelectorAll('.wire-overlay-box').length).toBe(1);
  cancelWire();
  expect(stage().querySelectorAll('.wire-overlay-box').length).toBe(0);
});
