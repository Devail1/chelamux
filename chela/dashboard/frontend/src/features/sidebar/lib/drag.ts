// CMX-380: drag a group header (or a single-session folder row) to reorder the groups, and
// drag a session onto a custom group to move it there. Plain pointer events, no library: the
// two drags are a vertical list's, and what a drop WRITES is the controller's (dropGroup /
// dropOnGroup, the same writes as Move up / Move down and Move to group).
//
//   mouse / pen  the drag starts once the pointer moves DRAG_THRESHOLD_PX: a click under it
//                still opens the row or folds the group
//   touch        a long press (LONG_PRESS_DRAG_MS) starts it; a finger that moves first is a
//                scroll and never starts one, nor does a finger that lands on a list still
//                gliding from a flick (CMX-435: the browser will not let the drag hold it)
//   Esc          cancels; so does a pointercancel
//
// CMX-381: under the Manual sort a session row also drags up or down WITHIN its group, with a
// drop line between rows (dropRow, the group's persisted row order). CMX-390: a child session
// drags only among its siblings (its `list`, the parent's `~kids:` key), never onto a group:
// it goes where its parent goes.
//
// The pinned sections (Needs you, Dispatched, Archived) are not draggable and take no drop.
// While a drag runs, the list takes no click: the click that ends a drag opens nothing.
import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { ARCHIVED_SECTION_KEY, DISPATCHED_KEY, NEEDS_YOU_KEY, UNGROUPED_KEY } from '../model.ts';

export const DRAG_THRESHOLD_PX = 5;
export const LONG_PRESS_DRAG_MS = 400;
/** A finger that moves this far before the long press is up is scrolling. */
export const TOUCH_SLOP_PX = 8;
/** Near the scroller's top or bottom edge (px), a drag scrolls the list. */
const EDGE_PX = 36;

export type DragSource =
  | { kind: 'group'; key: string }
  /** A session row: its assignment key (model.assignKey), the group it sits in now, and
   * (CMX-381) whether it may be put in a new place within that group. */
  | { kind: 'row'; assign: string; group: string; item: string; reorder?: boolean;
      /** CMX-390: a child's sibling list (`~kids:<parent>`): it reorders there and drops on no group. */
      list?: string };

/** What the list draws while a drag runs. */
export interface DragView {
  source: DragSource;
  /** Group reorder: the line where the group will land. */
  line: { key: string; side: 'before' | 'after' } | null;
  /** Row drop: the group under the pointer (UNGROUPED_KEY: on the list, outside every group). */
  over: { key: string; ok: boolean } | null;
  /** CMX-381: a row reordered within its group: the line between rows (by item key). */
  rowLine?: { key: string; side: 'before' | 'after' } | null;
}

/** Where a dragged group lands: `before` (null = at the end) for the drop, `line` for the
 * view. `groups` are the movable groups top to bottom with their vertical extent. A drop
 * that would leave the order as it is shows no line and drops nothing. */
export function groupSlot(groups: { key: string; top: number; bottom: number }[], dragged: string, y: number):
  { before: string | null; line: DragView['line'] } {
  const at = groups.findIndex(g => y < (g.top + g.bottom) / 2);
  const before = at < 0 ? null : groups[at]?.key ?? null;
  const i = groups.findIndex(g => g.key === dragged);
  const next = i < 0 ? undefined : groups[i + 1]?.key ?? null;
  if (before === dragged || (i >= 0 && before === next)) return { before, line: null };
  const last = groups[groups.length - 1];
  const line = before != null ? { key: before, side: 'before' as const } : last ? { key: last.key, side: 'after' as const } : null;
  return { before, line };
}

const isPinned = (el: Element) =>
  el.classList.contains('side-triage') || el.classList.contains('side-archived')
  || (el as HTMLElement).dataset.g === DISPATCHED_KEY || (el as HTMLElement).dataset.g === ARCHIVED_SECTION_KEY;

const sections = (root: Element) => [...root.children].filter(el =>
  el.classList.contains('side-group') || el.classList.contains('side-triage')) as HTMLElement[];

/** The movable groups in `root`, top to bottom (pinned and flat ones are not). */
function movableGroups(root: Element) {
  return sections(root)
    .filter(el => el.classList.contains('side-group') && !el.classList.contains('flat') && !isPinned(el) && el.dataset.g)
    .map(el => {
      const r = el.getBoundingClientRect();
      return { key: el.dataset.g as string, top: r.top, bottom: r.bottom };
    });
}

/** The rows of group `key` in `root`, top to bottom, by item key: its top-level rows, or
 * (CMX-390) with `list`, the children drawn under that parent. */
function groupRows(root: Element, key: string, list?: string) {
  const sec = sections(root).find(el => el.dataset.g === key);
  if (!sec) return [];
  const box = list ? [...sec.querySelectorAll<HTMLElement>('.tree-kids')].find(el => el.dataset.list === list) : null;
  const rows = box ? [...box.children].filter(el => el.matches('.agent-row[data-item]'))
    : list ? [] : [...sec.querySelectorAll<HTMLElement>('.group-rows > .agent-row[data-item]')];
  return (rows as HTMLElement[]).map(el => {
    const r = el.getBoundingClientRect();
    return { key: el.dataset.item as string, top: r.top, bottom: r.bottom };
  });
}

function sameView(a: DragView | null, b: DragView | null): boolean {
  if (!a || !b) return a === b;
  return a.source === b.source && a.line?.key === b.line?.key && a.line?.side === b.line?.side
    && a.over?.key === b.over?.key && a.over?.ok === b.over?.ok
    && a.rowLine?.key === b.rowLine?.key && a.rowLine?.side === b.rowLine?.side;
}

const inside = (r: DOMRect, x: number, y: number) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;

/** The section under (x, y): a group's key, UNGROUPED_KEY for the list outside every group,
 * null off the list. `pinned` when it is a pinned section. */
function sectionAt(root: Element, x: number, y: number): { key: string; pinned: boolean } | null {
  for (const el of sections(root)) {
    if (!inside(el.getBoundingClientRect(), x, y)) continue;
    return { key: el.dataset.g ?? NEEDS_YOU_KEY, pinned: isPinned(el) };
  }
  return inside(root.getBoundingClientRect(), x, y) ? { key: UNGROUPED_KEY, pinned: false } : null;
}

function scroller(el: Element | null): HTMLElement | null {
  for (let e = el?.parentElement ?? null; e; e = e.parentElement) {
    const oy = getComputedStyle(e).overflowY;
    if ((oy === 'auto' || oy === 'scroll') && e.scrollHeight > e.clientHeight) return e;
  }
  return null;
}

/** Started on a control inside the row or header (⋯, +, Unarchive): that control's, not a drag. */
const onControl = (t: EventTarget | null) =>
  t instanceof Element && !!t.closest('button, a, input, select, textarea, [role="menu"], [role="menuitem"]');

/** The desktop's folded icon rail draws no grip and takes no drag. */
const onRail = () => document.body.classList.contains('sidebar-collapsed')
  && !window.matchMedia?.('(max-width: 768px)').matches;

interface Session {
  source: DragSource;
  el: HTMLElement;
  root: Element;
  id: number;
  touch: boolean;
  x0: number;
  y0: number;
  x: number;
  y: number;
  active: boolean;
  /** CMX-435: false once the touch's touchstart came uncancelable (see the list's effect). */
  holdable: boolean;
  timer?: ReturnType<typeof setTimeout>;
  raf?: number;
  done(): void;
}

export interface DragDrop {
  /** A group dropped just before `before` (null: at the end). */
  group(key: string, before: string | null): unknown;
  /** A row dropped on group `groupKey`: may it go there (a custom group, Ungrouped)? */
  accepts(groupKey: string): boolean;
  row(assign: string, groupKey: string): unknown;
  /** CMX-381: row `item` of group `groupKey` dropped just before row `before` (null: last). */
  reorder(groupKey: string, item: string, before: string | null): unknown;
}

/** The list's drag: `view` to draw, and `bind(source)` for the pointerdown of a header or row. */
export function useSidebarDrag(drop: DragDrop) {
  const [view, setViewRaw] = useState<DragView | null>(null);
  // a pointermove that changes nothing on screen re-renders nothing
  const setView = useCallback((v: DragView | null) => setViewRaw(prev => (sameView(prev, v) ? prev : v)), []);
  const cur = useRef<Session | null>(null);
  const dropRef = useRef(drop);
  useEffect(() => { dropRef.current = drop; });

  // A long-press drag on a phone must hold the list still: touchmove is cancelled while one
  // runs. Registered once, NOT passive, on the list (a listener added mid-gesture may be
  // too late for the browser to honour); a touch with no drag running scrolls as before.
  useEffect(() => {
    const list = document.getElementById('sidebar-agents');
    if (!list) return undefined;
    const hold = (e: TouchEvent) => { if (cur.current?.active && e.cancelable) e.preventDefault(); };
    // CMX-435: a finger that lands on the list while it still glides from a flick stops the
    // glide, and Chrome sends that touch's touchstart and every touchmove UNCANCELABLE: `hold`
    // cannot keep the list still, so the first move would pan it and pointercancel the drag
    // (a drag that starts, then vanishes under the finger). Such a touch only stops the
    // glide; its long press arms no drag. Pointerdown comes before touchstart, so the
    // session is already there to mark. Passive: it only reads. A touch on a list at rest
    // comes cancelable because `hold` above is a blocking (non-passive) touch listener.
    const caught = (e: TouchEvent) => { if (cur.current && !e.cancelable) cur.current.holdable = false; };
    list.addEventListener('touchmove', hold, { passive: false });
    list.addEventListener('touchstart', caught, { passive: true });
    return () => {
      list.removeEventListener('touchmove', hold);
      list.removeEventListener('touchstart', caught);
    };
  }, []);

  useEffect(() => () => cur.current?.done(), []);

  const bind = useCallback((source: DragSource) => (e: ReactPointerEvent<HTMLElement>) => {
    if (cur.current || !e.isPrimary || onControl(e.target) || onRail()) return;
    const touch = e.pointerType === 'touch';
    if (!touch && e.button !== 0) return;
    const el = e.currentTarget;
    const sec = el.closest('.side-group, .side-triage');
    const root = sec?.parentElement;
    if (!root) return;

    const paint = (s: Session) => {
      if (s.source.kind === 'group') {
        const { line } = groupSlot(movableGroups(s.root), s.source.key, s.y);
        setView({ source: s.source, line, over: null });
        return;
      }
      const at = sectionAt(s.root, s.x, s.y);
      if (s.source.reorder && at?.key === s.source.group) {
        const { line } = groupSlot(groupRows(s.root, s.source.group, s.source.list), s.source.item, s.y);
        setView({ source: s.source, line: null, over: null, rowLine: line });
        return;
      }
      const ok = !s.source.list && !!at && !at.pinned && at.key !== s.source.group && dropRef.current.accepts(at.key);
      setView({ source: s.source, line: null, over: at ? { key: at.key, ok } : null });
    };

    // Near an edge of the list's scroller, keep scrolling while the pointer rests there.
    const edge = (s: Session) => {
      const sc = scroller(s.root);
      if (!sc) return;
      const r = sc.getBoundingClientRect();
      const dy = s.y < r.top + EDGE_PX ? -8 : s.y > r.bottom - EDGE_PX ? 8 : 0;
      if (!dy || typeof requestAnimationFrame !== 'function') return;
      s.raf = requestAnimationFrame(() => {
        s.raf = undefined;
        if (cur.current !== s || !s.active) return;
        sc.scrollTop += dy;
        paint(s);
        edge(s);
      });
    };

    const begin = (s: Session) => {
      s.active = true;
      clearTimeout(s.timer);
      document.body.classList.add('sidebar-dragging');
      window.getSelection?.()?.removeAllRanges();
      paint(s);
    };

    // The click that ends a drag (or a long press that armed one) opens nothing. A drag that
    // ends with no click (a touch) must not eat the NEXT tap's: the next pointerdown, or a
    // short timeout, takes the guard down.
    const swallowClick = () => {
      const eat = (ev: Event) => {
        ev.stopPropagation();
        ev.preventDefault();
        off();
      };
      const off = () => {
        window.removeEventListener('click', eat, true);
        window.removeEventListener('pointerdown', off, true);
        clearTimeout(t);
      };
      const t = setTimeout(off, 400);
      window.addEventListener('click', eat, true);
      window.addEventListener('pointerdown', off, true);
    };

    const commit = (s: Session) => {
      if (s.source.kind === 'group') {
        const { before, line } = groupSlot(movableGroups(s.root), s.source.key, s.y);
        if (line) dropRef.current.group(s.source.key, before);
        return;
      }
      const at = sectionAt(s.root, s.x, s.y);
      if (s.source.reorder && at?.key === s.source.group) {
        const { before, line } = groupSlot(groupRows(s.root, s.source.group, s.source.list), s.source.item, s.y);
        if (line) dropRef.current.reorder(s.source.list ?? s.source.group, s.source.item, before);
        return;
      }
      if (s.source.list || !at || at.pinned || at.key === s.source.group || !dropRef.current.accepts(at.key)) return;
      dropRef.current.row(s.source.assign, at.key);
    };

    const move = (ev: PointerEvent) => {
      const s = cur.current;
      if (!s || ev.pointerId !== s.id) return;
      s.x = ev.clientX;
      s.y = ev.clientY;
      const far = Math.hypot(s.x - s.x0, s.y - s.y0);
      if (!s.active) {
        if (s.touch) { if (far > TOUCH_SLOP_PX) s.done(); } // a scroll, not a drag
        else if (far >= DRAG_THRESHOLD_PX) begin(s);
        return;
      }
      if (s.raf == null) edge(s);
      paint(s);
    };
    const up = (ev: PointerEvent) => {
      const s = cur.current;
      if (!s || ev.pointerId !== s.id) return;
      if (s.active) {
        s.x = ev.clientX;
        s.y = ev.clientY;
        swallowClick();
        commit(s);
      }
      s.done();
    };
    const cancel = (ev: PointerEvent) => {
      if (cur.current && ev.pointerId === cur.current.id) cur.current.done();
    };
    const key = (ev: KeyboardEvent) => {
      const s = cur.current;
      if (ev.key !== 'Escape' || !s?.active) return;
      ev.preventDefault();
      ev.stopPropagation();
      swallowClick();
      s.done();
    };
    // A long press on a phone also asks for the row's context menu: not while it drags.
    // The window lost the pointer (a release over a terminal iframe, a tab switch): cancel.
    const lost = () => cur.current?.done();
    const menu = (ev: Event) => {
      if (!cur.current?.active) return;
      ev.preventDefault();
      ev.stopPropagation();
    };

    const s: Session = {
      source, el, root, id: e.pointerId, touch, x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY, active: false, holdable: true,
      done: () => {
        clearTimeout(s.timer);
        if (s.raf != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(s.raf);
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        window.removeEventListener('pointercancel', cancel);
        window.removeEventListener('keydown', key, true);
        window.removeEventListener('contextmenu', menu, true);
        window.removeEventListener('blur', lost);
        document.body.classList.remove('sidebar-dragging');
        if (cur.current === s) cur.current = null;
        setView(null);
      },
    };
    cur.current = s;
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', cancel);
    window.addEventListener('keydown', key, true);
    window.addEventListener('contextmenu', menu, true);
    window.addEventListener('blur', lost);
    if (touch) s.timer = setTimeout(() => { if (cur.current === s && s.holdable) begin(s); }, LONG_PRESS_DRAG_MS);
  }, [setView]);

  return { view, bind };
}
