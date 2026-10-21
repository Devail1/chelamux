// The phone keybar's pure logic (CMX-237, FRONTEND_PORT_PLAN.md P29): the sticky-Ctrl cycle,
// the keys it draws, and the geometry that pins it above the soft keyboard. No DOM.

/** Ctrl, modeled on Moshi's terminal keyboard: a tap cycles off → armed → locked → off.
 * Armed is consumed by one Ctrl-letter; locked stays for sequences (^C ^D ^Z). */
export type CtrlState = 'off' | 'armed' | 'locked';

export function nextCtrl(state: CtrlState): CtrlState {
  return state === 'off' ? 'armed' : state === 'armed' ? 'locked' : 'off';
}

/** The state after a Ctrl-letter was sent. */
export function afterCtrlLetter(state: CtrlState): CtrlState {
  return state === 'armed' ? 'off' : state;
}

/** A key: the name /api/term/key takes, its face, and its tooltip / accessible name. */
export interface Key {
  key: string;
  face: string;
  title?: string;
  label?: string;
}

const k = (key: string, face = key, title?: string, label?: string): Key => ({ key, face, title, label });

/** The Ctrl-letter layer, revealed while Ctrl is engaged. */
export const CTRL_LETTERS: Key[] = [
  k('a', 'A', 'Ctrl-A'), k('d', 'D', 'Ctrl-D (EOF)'), k('e', 'E', 'Ctrl-E'), k('k', 'K', 'Ctrl-K'),
  k('l', 'L', 'Ctrl-L (clear)'), k('r', 'R', 'Ctrl-R (search)'), k('u', 'U', 'Ctrl-U'),
  k('w', 'W', 'Ctrl-W'), k('z', 'Z', 'Ctrl-Z (suspend)'),
];

export const ARROW_FACE = { Left: '←', Up: '↑', Down: '↓', Right: '→' } as const;
export type Arrow = keyof typeof ARROW_FACE;

/** The phone row's arrows, left to right. */
export const ARROWS: Key[] = (['Left', 'Up', 'Down', 'Right'] as const).map((a) => k(a, ARROW_FACE[a], undefined, a));

export const CHARS: Key[] = ['|', '/', '\\', '~', '-', '_'].map((c) => k(c));

export const PAGING: Key[] = [k('Home'), k('End'), k('PageUp', 'PgUp'), k('PageDown', 'PgDn')];

/** The desktop bar's 2-column navigation block, row by row. */
export const DESK_NAV: Key[] = [k('Home', 'Home', 'Home'), k('PageUp', 'PgUp', 'Page Up'), k('End', 'End', 'End'), k('PageDown', 'PgDn', 'Page Down')];

/** How much of the layout viewport's bottom the soft keyboard covers. Phone only: desktop
 * browser zoom also moves the visual viewport, and must not resize wall tiles. */
export function keyboardOccluded(o: { mobile: boolean; innerHeight: number; vvTop: number; vvHeight: number }): number {
  return o.mobile ? Math.max(0, Math.round(o.innerHeight - (o.vvTop + o.vvHeight))) : 0;
}

/** The keybar's `top`: its bottom on the visual viewport's bottom edge (stable through iOS
 * rubber-band scrolling, unlike a translated bottom:0 bar). */
export function pinTop(o: { vvTop: number; vvHeight: number; barHeight: number }): number {
  return Math.max(0, Math.round(o.vvTop + o.vvHeight - o.barHeight));
}

/** CMX-102: the phone pane's height while the keyboard is up, MEASURED so its box ends at
 * the keybar's top (the CSS calc misses chrome it does not list). Floored, so a fractional
 * box never reaches 1px into the bar. Keyboard closed or desktop: null, the CSS stays. */
export function paneFitHeight(o: {
  mobile: boolean;
  occluded: number;
  vvTop: number;
  vvHeight: number;
  barHeight: number;
  paneTop: number;
}): number | null {
  if (!o.mobile || !(o.occluded > 0)) return null;
  return Math.max(0, Math.floor(o.vvTop + o.vvHeight - o.barHeight - o.paneTop));
}
