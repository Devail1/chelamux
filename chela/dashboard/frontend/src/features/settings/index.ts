// CMX-221 (CMX-212 P15-P18): the Settings modal (CMX-287) and its tabs. `model.ts` is its other
// entry point. The prefs it writes are read back by the pane shims (app.py) and presence.
export { Settings } from './lib/Settings.tsx';
export { closeSettings, config, openSettings, selectSettingsTab, setSettingsQuery, settings, toggleSettings } from './lib/state.ts';
export { updateDeps } from './lib/General.tsx';
export { applySavedTheme, setTheme } from './lib/prefs.ts';
