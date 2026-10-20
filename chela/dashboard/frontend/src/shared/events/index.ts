// CMX-217 (CMX-212 P03): the page's ONE EventSource on /api/events, an accelerator over the
// poll timers. Every frame is a NOTIFICATION: it carries no data the UI renders directly, so a
// dropped frame is never a lost fact, and if the stream never connects or drops, the browser
// reconnects and the polls cover the gap. Each feature subscribes to the events it owns
// (onEvent), so this file knows no feature.
import { BASE_PATH } from '../api/index.ts';

/** Every event the server sends (chela/dashboard/app.py `/api/events`). `hello` opens every
 * connection, reconnects included. */
export const EVENT_NAMES = ['hello', 'windows', 'runs', 'log', 'term-ready', 'orchestrator', 'overrides'] as const;
export type EventName = (typeof EVENT_NAMES)[number];

/** A frame's parsed JSON, or null when it carries none (a trigger-only frame). */
export type Handler = (data: unknown) => void;

const handlers = new Map<EventName, Set<Handler>>();

/** Run `handler` on every `name` frame, in subscription order. Returns the unsubscribe. */
export function onEvent(name: EventName, handler: Handler): () => void {
  let set = handlers.get(name);
  if (!set) handlers.set(name, (set = new Set()));
  set.add(handler);
  return () => {
    set.delete(handler);
  };
}

function parse(data: unknown): unknown {
  try {
    return JSON.parse(String(data));
  } catch {
    return null;
  }
}

/** Hand one frame to `name`'s subscribers. A throwing subscriber is logged, and the others
 * still run: one feature's bug never silences another's updates. */
export function dispatch(name: EventName, raw: unknown): void {
  const data = parse(raw);
  for (const handler of [...(handlers.get(name) ?? [])]) {
    try {
      handler(data);
    } catch (e) {
      console.error(`SSE ${name} handler:`, e);
    }
  }
}

/** Open the stream. null when the browser has no EventSource or refuses to open one: the page
 * then runs on its polls alone, as it did before SSE existed. */
export function openEvents(win: Window = window): EventSource | null {
  const Source = (win as Window & { EventSource?: typeof EventSource }).EventSource;
  if (!Source) return null;
  let src: EventSource;
  try {
    src = new Source(`${BASE_PATH}/api/events`);
  } catch (e) {
    console.warn('SSE unavailable, polling-only:', e);
    return null;
  }
  for (const name of EVENT_NAMES) src.addEventListener(name, (e) => dispatch(name, (e as MessageEvent).data));
  // The browser reconnects on its own and the polls cover the gap: nothing to do here.
  src.onerror = () => {};
  return src;
}
