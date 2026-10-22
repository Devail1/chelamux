// CMX-221 (CMX-212 P19): the ⌘K palette, the keyboard shortcuts sheet and the page's global keys.
// `model.ts` is its other entry point.
export { Palette, Shortcuts } from './lib/Palette.tsx';
export { installPalette } from './lib/keys.ts';
export { closePalette, closeShortcuts, openPalette, openShortcuts, palette, paletteRows, runPalette, setPaletteHost, shortcuts, sidebarJumpInput, togglePalette, type PaletteHost } from './lib/state.ts';
