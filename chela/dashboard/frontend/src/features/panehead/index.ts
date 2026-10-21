// CMX-241 (CMX-212 P26): a Wall pane's header, portaled into each tile legacy terminals.js
// builds, and the labels the sidebar, palette and dock share with it.
export { configurePaneHeads, mountPaneHeads, refreshPaneHeads } from './lib/mount.tsx';
export { PaneHead, type PaneHeadHost, type PaneHeadProps } from './lib/PaneHead.tsx';
export { claudeTitle, displayLabel, dotState, DOT_FOR_STATE, headLabel, headState, isShell, paneSubtitle, wantsHuman, type HeadAgent, type HeadLabel } from './model.ts';
