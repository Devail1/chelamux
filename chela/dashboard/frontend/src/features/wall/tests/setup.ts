// What each Wall test starts from: a fake Gridstack (a node per adopted tile, every call
// recorded), the Wall panel's ids, the stores reset, and a fetch stub routed by path.
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { view } from '../../../shared/store/view.ts';
import { DEFAULT_PRESET, minimizedPanes, pinnedPanes, wallLayout } from '../../../shared/store/wall.ts';
import { forgetReady, markReady, readiness } from '../../paneframe/index.ts';

export type Cell = { x: number; y: number; w: number; h: number };
type Node = Cell & { id: string; el: HTMLElement };

/** A Gridstack stand-in: its engine holds what was adopted, `update` moves a node (and its
 * gs-* attributes, as the real one does), and every call lands in `calls`. */
export function fakeGridStack() {
  const calls: unknown[][] = [];
  const handlers: Record<string, (e: Event, el: HTMLElement) => void> = {};
  let nodes: Node[] = [];
  const adopted: Record<string, Cell> = {}; // each tile's spot when Gridstack adopted it
  const nodeOf = (el: HTMLElement): Node => {
    const n = (k: string) => Number(el.getAttribute(`gs-${k}`));
    return { id: el.getAttribute('gs-id') ?? '', x: n('x'), y: n('y'), w: n('w'), h: n('h'), el };
  };
  const adopt = (el: HTMLElement) => {
    const node = nodeOf(el);
    adopted[node.id] = { x: node.x, y: node.y, w: node.w, h: node.h };
    nodes.push(node);
    (el as HTMLElement & { gridstackNode?: Node }).gridstackNode = node;
  };
  const grid = {
    engine: { get nodes() { return nodes; } },
    on: (ev: string, fn: (e: Event, el: HTMLElement) => void) => { handlers[ev] = fn; },
    save: () => nodes.map(({ id, x, y, w, h }) => ({ id, x, y, w, h })),
    makeWidget: (el: HTMLElement) => { calls.push(['makeWidget', el.getAttribute('gs-id')]); adopt(el); },
    removeWidget: (el: HTMLElement, dom?: boolean) => {
      calls.push(['removeWidget', el.getAttribute('gs-id'), dom]);
      nodes = nodes.filter((n) => n.el !== el);
      if (dom !== false) el.remove();
    },
    destroy: (dom?: boolean) => { calls.push(['destroy', dom]); },
    cellHeight: (px: number) => { calls.push(['cellHeight', px]); },
    batchUpdate: (on?: boolean) => { calls.push(['batchUpdate', on]); },
    update: (el: HTMLElement, o: Partial<Cell>) => {
      const node = nodes.find((n) => n.el === el);
      if (!node) return;
      Object.assign(node, o);
      for (const k of ['x', 'y', 'w', 'h'] as const) el.setAttribute(`gs-${k}`, String(node[k]));
    },
    enableMove: (on: boolean) => { calls.push(['enableMove', on]); },
    enableResize: (on: boolean) => { calls.push(['enableResize', on]); },
  };
  const init = vi.fn((_opts: object, el: HTMLElement) => {
    nodes = [];
    el.querySelectorAll<HTMLElement>('.grid-stack-item').forEach(adopt);
    return grid;
  });
  /** Fire a Gridstack event as a drag or resize would. */
  const fire = (ev: string, el?: HTMLElement) => handlers[ev]?.(new Event(ev), el as HTMLElement);
  const cell = (wid: string): Cell | undefined => {
    const n = nodes.find((x) => x.id === wid);
    return n && { x: n.x, y: n.y, w: n.w, h: n.h };
  };
  return { init, calls, fire, cell, adopted, nodes: () => nodes };
}

export let gs = fakeGridStack();
/** A new fake Gridstack for what comes next (the one before is dropped). */
export function freshGrid(): void {
  gs = fakeGridStack();
  vi.stubGlobal('GridStack', { init: gs.init });
}
export let phone = false;
export const setPhone = (on: boolean) => { phone = on; };

export function agent(wid: string, extra: Record<string, unknown> = {}) {
  return { name: `n${wid}`, window_id: wid, online: true, session_id: `s${wid}`, session_status: 'idle', ...extra };
}

export type Call = { path: string; method: string };
export let calls: Call[] = [];
let route: (path: string) => unknown = () => ({});
/** Answer each fetch with `fn(path)` (200, JSON); a readiness probe is always up. */
export function serve(fn: (path: string) => unknown): void { route = fn; }
const answer = (path: string) => (path.includes('/api/term/ready') ? { ready: true } : route(path));

beforeEach(() => {
  localStorage.clear();
  document.body.innerHTML = '<div id="panel-terminals"><span id="wall-toolbar-island"></span><div id="term-stage"></div><div id="term-min-dock" hidden></div></div>';
  freshGrid();
  phone = false;
  vi.stubGlobal('matchMedia', (q: string) => ({ media: q, matches: phone, addEventListener() {}, removeEventListener() {} }));
  calls = [];
  route = () => ({});
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    calls.push({ path: String(url), method: init?.method ?? 'GET' });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(answer(String(url))) });
  });
  agents.set([]);
  view.set('terminals');
  minimizedPanes.set(new Set());
  pinnedPanes.set(new Set());
  wallLayout.set({ preset: DEFAULT_PRESET, locked: false, auto: false, focus: false });
  for (const wid of [...readiness.get().keys()]) forgetReady(wid);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** Mark `wids` ready, so the readiness probe never fetches and a frame draws at once. */
export const ready = (...wids: string[]) => wids.forEach(markReady);
export const tile = (wid: string) => document.querySelector<HTMLElement>(`#term-stage .grid-stack-item[gs-id="${wid}"]`);
export const tiles = () => [...document.querySelectorAll('#term-stage .grid-stack-item')].map((t) => t.getAttribute('gs-id'));
export const saved = () => JSON.parse(localStorage.getItem('pc_wall_layout') || 'null') as Record<string, Cell> | null;
export async function settle(): Promise<void> {
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
}
