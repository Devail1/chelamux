// The Wall's display mode, as terminals.js renders it: 'single' (one pane) or 'wall' (the
// grid); null until its first render. The keybar island reads it (CMX-237).
import { createStore } from '../bridge/index.ts';

export const termMode = createStore<string | null>(null);
