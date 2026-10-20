// CMX-218 (CMX-212 P04): a viewport question (FRONTEND_PORT_PLAN.md §2.7), replacing the
// legacy matchMedia calls as they port. Re-renders when the answer changes.
import { useCallback, useSyncExternalStore } from 'react';

export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const mql = window.matchMedia(query);
      mql.addEventListener('change', onChange);
      return () => mql.removeEventListener('change', onChange);
    },
    [query],
  );
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches, () => false);
}
