// The active view: 'terminals' (the Wall), 'work' or 'agent-detail' (views.js). Legacy reads
// it as util.js's `currentTab`; the scheduler ticks the view it names. 'work' until a view is
// selected, the view the page shows with terminals off.
import { createStore } from '../bridge/index.ts';

export const view = createStore<string>('work');
