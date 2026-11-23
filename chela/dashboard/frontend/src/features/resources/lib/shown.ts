// CMX-492: Settings > Appearance "Show resource stats", per device (this browser's storage), on
// by default. Off means no strip AND no /api/resources request (Resources.tsx reads it for both).
import { createStore } from '../../../shared/store/index.ts';

const KEY = 'chela_hide_resource_stats';

/** Only a stored '1' hides it: unset, unreadable or unknown is the default, on. */
function read(): boolean {
  try {
    return localStorage.getItem(KEY) !== '1';
  } catch {
    return true;
  }
}

/** Whether this device shows (and polls for) the resource stats. */
export const resourcesShown = createStore(read());

export function setResourcesShown(on: boolean): void {
  try {
    if (on) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, '1');
  } catch {
    /* storage blocked: the choice holds for this page */
  }
  resourcesShown.set(on);
}

// The device's other open tabs follow at once; unsynced they would poll until a reload. A null
// key is localStorage.clear().
window.addEventListener('storage', (e) => {
  if (e.key === KEY || e.key === null) resourcesShown.set(read());
});
