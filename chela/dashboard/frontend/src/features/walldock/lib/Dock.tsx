// CMX-242 (CMX-212 P28): the Minimized tray under the Wall: one chip per minimized pane (its
// status dot, its label), a click restores it. ONE row whatever the count (Liav 2026-10-19): the
// chips scroll sideways, a fade marks the edge that hides more, and a vertical wheel scrolls it
// horizontally. Chips glide to their new place when the recency order changes. From
// terminals.js `renderMinDock`.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { stateTitle, tileState } from '../../../../../static/js/wallmodel.ts';
import { icons } from '../../../shared/icons/index.ts';
import { TextButton } from '../../../shared/ui/index.ts';
import { displayLabel, dotState, wantsHuman, type HeadAgent } from '../../panehead/index.ts';
import { windowRunDot, type AgentRow } from '../../sidebar/model.ts';

export interface DockProps {
  /** The minimized panes, in chip order. */
  wids: readonly string[];
  agents: ReadonlyMap<string, HeadAgent>;
  restore(wid: string): void;
}

function Chip({ wid, agent, restore }: { wid: string; agent: HeadAgent | undefined; restore(wid: string): void }) {
  const wants = wantsHuman(agent);
  // CMX-137: a dispatched run's window wears the run's own progress icon (the sidebar row's).
  const run = windowRunDot(agent as AgentRow | undefined, wants);
  const label = displayLabel(agent, wid);
  return (
    <TextButton className={wants ? 'min-chip min-chip-minimized term-waiting' : 'min-chip min-chip-minimized'} data-wid={wid}
      title={`Click to restore ${label}`} onClick={() => restore(wid)}>
      <span className={`term-status-dot ${run ? run.cls : dotState(agent)}`} data-status-for={wid}
        title={run ? run.label : stateTitle(agent, tileState(agent, wants))}
        {...(run ? { role: 'img', 'aria-label': run.label } : {})} />
      <span className="min-chip-title">{label}</span>
      <span className="min-chip-icon" aria-hidden="true">
        <icons.maximize size={12} />
      </span>
    </TextButton>
  );
}

export function Dock({ wids, agents, restore }: DockProps) {
  const strip = useRef<HTMLDivElement>(null);
  const [fade, setFade] = useState({ start: false, end: false });
  // Each chip's offset after the last paint: the FLIP's "first" for the next reorder.
  const lefts = useRef(new Map<string, number>());

  const measure = useCallback(() => {
    const s = strip.current;
    if (!s) return;
    const start = s.scrollLeft > 1;
    const end = s.scrollLeft + s.clientWidth < s.scrollWidth - 1;
    setFade((f) => (f.start === start && f.end === end ? f : { start, end }));
  }, []);

  useLayoutEffect(() => {
    measure();
    const s = strip.current;
    if (!s) return;
    const prev = lefts.current;
    const next = new Map<string, number>();
    s.querySelectorAll<HTMLElement>('.min-chip').forEach((c) => {
      const wid = c.dataset.wid ?? '';
      const now = c.offsetLeft;
      next.set(wid, now);
      const was = prev.get(wid);
      if (was === undefined || was === now) return; // new chip, or did not move
      c.style.transition = 'none';
      c.style.transform = `translateX(${was - now}px)`;
      requestAnimationFrame(() => {
        c.style.transition = 'transform 160ms ease';
        c.style.transform = '';
      });
    });
    lefts.current = next;
  }, [wids, measure]);

  useEffect(() => {
    const s = strip.current;
    if (!s) return;
    // A vertical wheel scrolls the row sideways (desktop); a touch swipe scrolls it natively.
    // Not React's onWheel: that listener is passive, so it could not keep the page still.
    const wheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX) || s.scrollWidth <= s.clientWidth) return;
      e.preventDefault();
      s.scrollLeft += e.deltaY;
    };
    s.addEventListener('wheel', wheel, { passive: false });
    window.addEventListener('resize', measure);
    return () => {
      s.removeEventListener('wheel', wheel);
      window.removeEventListener('resize', measure);
    };
  }, [measure]);

  const cls = ['min-dock-chips', fade.start && 'fade-start', fade.end && 'fade-end'].filter(Boolean).join(' ');
  return (
    <>
      <span className="min-dock-label">Minimized ({wids.length})</span>
      <div className={cls} ref={strip} onScroll={measure}>
        {wids.map((wid) => <Chip key={wid} wid={wid} agent={agents.get(wid)} restore={restore} />)}
      </div>
    </>
  );
}
