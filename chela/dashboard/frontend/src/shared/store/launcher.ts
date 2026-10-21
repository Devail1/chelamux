// CMX-233: the launcher's favorite and recent project dirs (/api/launcher). The launch menu
// (features/launcher, CMX-219) reads and writes them; the Work view's "+ Init a repo" suggests
// them as paths.
import { createStore } from '../bridge/index.ts';

/** One project dir. `label` and `exists` come from /api/launcher; a row without a path is skipped. */
export type LauncherEntry = { path?: string; label?: string; exists?: boolean } | null;
export type LauncherData = { favorites?: LauncherEntry[]; recent?: LauncherEntry[] };

export const launcher = createStore<LauncherData>({ recent: [], favorites: [] });
