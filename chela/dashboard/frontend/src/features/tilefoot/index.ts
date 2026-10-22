// CMX-242 (CMX-212 P27): a Wall pane's footer (the context bar and the action bar) and its
// recap line, portaled into each tile legacy terminals.js builds.
export { mountTileFoots, refreshTileFoots } from './lib/mount.tsx';
export { TileFoot, type TileFootProps } from './lib/TileFoot.tsx';
export { Recap, recapOpen, toggleRecap } from './lib/Recap.tsx';
export { ctxView, NO_CONTEXT, type CtxView } from './model.ts';
