// The Work view's per-browser choices that the phone's title bar and the board share
// (CMX-315): the project filter, the phone layout and the recently picked projects. WorkView
// owns them and hands them to the Board, so the switcher sheet and the board read one value.
import { useState } from 'react';
import { FILTER_KEY, loadFilter } from '../board.ts';
import { loadRecent, RECENT_KEY, touchRecent } from '../phone.ts';

// Every storage call inside a lambda, so a missing or throwing localStorage stays inside the
// models' try/catch: storage failing is the default view, never a crash.
export const LS = { getItem: (k: string) => localStorage.getItem(k), setItem: (k: string, v: string) => localStorage.setItem(k, v) };
export function save(key: string, value: string) {
  try {
    LS.setItem(key, value);
  } catch {
    /* the choice holds for this page */
  }
}
export function read(key: string): string | null {
  try {
    return LS.getItem(key);
  } catch {
    return null;
  }
}

/** The phone layout: Rows (the default, CMX-315) or the swiped Board. */
export type PhoneLayout = 'rows' | 'board';
export const LAYOUT_KEY = 'chela_work_phone_layout';

export type WorkPrefs = {
  filter: string[];
  pickFilter: (next: string[]) => void;
  layout: PhoneLayout;
  pickLayout: (next: PhoneLayout) => void;
  recent: string[];
  /** The switcher's single pick: one project, or every one for ''. */
  pickProject: (path: string) => void;
};

export function useWorkPrefs(): WorkPrefs {
  const [filter, setFilter] = useState(() => loadFilter(LS));
  const [layout, setLayout] = useState<PhoneLayout>(() => (read(LAYOUT_KEY) === 'board' ? 'board' : 'rows'));
  const [recent, setRecent] = useState(() => loadRecent(LS));
  function pickFilter(next: string[]) {
    setFilter(next);
    save(FILTER_KEY, JSON.stringify(next));
  }
  return {
    filter,
    pickFilter,
    layout,
    pickLayout(next) {
      setLayout(next);
      save(LAYOUT_KEY, next);
    },
    recent,
    pickProject(path) {
      pickFilter(path ? [path] : []);
      if (!path) return;
      const next = touchRecent(recent, path);
      setRecent(next);
      save(RECENT_KEY, JSON.stringify(next));
    },
  };
}
