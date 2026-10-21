// CMX-240: "now" for the override's countdown ("3h 5m left"), as an outside store so render
// stays pure. It ticks every 30 s while anything with a countdown reads it, and is fresh on
// each first read.
import { useSyncExternalStore } from 'react';

const TICK_MS = 30_000;
let now = Date.now();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function subscribe(listener: () => void) {
  if (!listeners.size) {
    now = Date.now();
    timer = setInterval(() => {
      now = Date.now();
      for (const l of [...listeners]) l();
    }, TICK_MS);
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (!listeners.size && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

const idle = () => () => {};

/** Now, ticking only while `counting` (an override with a countdown is on screen); 0 else. */
export function useNow(counting: boolean) {
  return useSyncExternalStore(counting ? subscribe : idle, () => (counting ? now : 0));
}
