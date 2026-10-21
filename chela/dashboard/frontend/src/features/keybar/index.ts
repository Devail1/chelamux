// CMX-237 (CMX-212 P29): the on-screen keys under the single pane. `model.ts` (the pure
// logic) is this module's other entry point. terminals.js calls pinKeybar after it rebuilds a
// pane or a pane's terminal loads.
export { Keybar, type KeybarActions } from './lib/Keybar.tsx';
export { legacyKeybarActions, mountKeybar } from './lib/mount.tsx';
export { pinKeybar, refitPaneTerm } from './lib/pin.ts';
