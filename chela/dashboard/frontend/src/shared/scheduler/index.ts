// CMX-217 (CMX-212 P03): the page's refresh loop, every REFRESH_MS and on every view switch.
// Each feature registers its own lane from its own module (registerLane), so a new poll is a
// new line in its feature, not an edit here. The rules (CMX-166):
//   - lanes are independent, not a chain: one that throws is logged and the rest still run;
//   - ONE /api/agents/context read per tick, shared by every lane that needs it;
//   - the active view's tick starts as soon as a lane reports the pane list known (the Wall
//     needs only that), not after every other read;
//   - the first tick IS the boot: it seeds every view-independent read, there is no second pass.
import { BOOT, api } from '../api/index.ts';
import { view } from '../store/view.ts';

/** What one tick hands its lanes. */
export interface Tick {
  /** true on the page's first tick only. */
  boot: boolean;
  /** This tick's one /api/agents/context read; null with terminals off. */
  context: Promise<unknown> | null;
  /** Call once the /api/agents pane list is cached: starts the active view's tick. */
  paneListKnown: () => void;
  /** Settles once the active view's tick has (its first paint), rejected or not. */
  viewPainted: Promise<unknown>;
}

/** `tick`: awaited by the tick, a throw logged. `detached`: not awaited, a throw swallowed
 * (the update check). `settled`: runs once every `tick` lane has settled, a throw logged. */
export type Phase = 'tick' | 'detached' | 'settled';

interface Lane {
  name: string;
  run: (t: Tick) => unknown;
  phase: Phase;
}

const lanes: Lane[] = [];
let viewTick: (name: string) => ((context: Promise<unknown> | null) => unknown) | null = () => null;
let booted = false;

/** Add a lane to every tick. A second registration under the same name replaces the first. */
export function registerLane(name: string, run: (t: Tick) => unknown, phase: Phase = 'tick'): void {
  const at = lanes.findIndex((l) => l.name === name);
  if (at >= 0) lanes.splice(at, 1);
  lanes.push({ name, run, phase });
}

/** How the scheduler finds a view's tick (views.js's registry), by the store's `view`. */
export function setViewTicks(find: typeof viewTick): void {
  viewTick = find;
}

// Started at once, as the old lanes array did; a synchronous throw becomes this lane's rejection.
function attempt(lane: Lane, t: Tick): Promise<unknown> {
  try {
    return Promise.resolve(lane.run(t));
  } catch (e) {
    return Promise.reject(e);
  }
}

/** One tick. Resolves once every `tick` lane has settled and the `settled` lanes have run. */
export async function refresh(): Promise<void> {
  const boot = !booted;
  booted = true;
  const context = BOOT.terminalsEnabled ? api('/api/agents/context') : null;
  let paneListKnown = () => {};
  const paneList = new Promise<void>((resolve) => {
    paneListKnown = resolve;
  });
  const tick = viewTick(view.get());
  const painted = paneList.then(() => (tick ? tick(context) : null));
  const t: Tick = { boot, context, paneListKnown, viewPainted: painted.catch(() => {}) };
  const running = [painted, ...lanes.filter((l) => l.phase === 'tick').map((l) => attempt(l, t))];
  for (const lane of lanes) if (lane.phase === 'detached') attempt(lane, t).catch(() => {});
  const results = await Promise.allSettled(running);
  for (const r of results) if (r.status === 'rejected') console.error('Refresh error:', r.reason);
  for (const lane of lanes) {
    if (lane.phase === 'settled') attempt(lane, t).catch((e: unknown) => console.error('Refresh error:', e));
  }
}

/** Arm the loop: every `intervalMs` from now. The first tick is the caller's (boot). */
export function startScheduler(intervalMs: number): ReturnType<typeof setInterval> {
  return setInterval(refresh, intervalMs);
}
