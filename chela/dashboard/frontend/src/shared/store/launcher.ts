// CMX-233: the launcher's favorite and recent project dirs (/api/launcher), as legacy
// launcher.js last read them; the Work view's "+ Init a repo" suggests them as paths.
import { createStore } from '../bridge/index.ts';

export type LauncherEntry = { path?: string } | null;
export type LauncherData = { favorites?: LauncherEntry[]; recent?: LauncherEntry[] };

export const launcher = createStore<LauncherData>({ recent: [], favorites: [] });
