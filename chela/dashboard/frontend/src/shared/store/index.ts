// CMX-217 (CMX-212 P03): the page's state, one file per slice (view.ts, agents.ts, ...), so
// parallel port slices add a file instead of editing a shared one. A slice is a Store
// (shared/bridge: get / set / subscribe); React reads one with useStore. Legacy imports the
// same objects, through util.js's facade (currentTab, _agentsCache).
export { createStore, useStore, type Store } from '../bridge/index.ts';
