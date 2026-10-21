// CMX-219 (CMX-212 P20): the "+ new" launch menu, its favorites picker and its two triggers
// (the sidebar's New session, the phone "+"); the launches legacy nav.js still calls (the ⌘K
// palette, a sidebar group's "+").
export { LaunchMenu, NewSessionButton, NewSessionFab, closeLaunchMenu, openLaunchMenu } from './lib/LaunchMenu.tsx';
export { forgetRecent, launchProject, launcher, newSandboxedSession, newShellWindow, pinFav, refreshLauncher, samePath, setLauncherHost, unpinFav, type LaunchEntry, type LauncherData, type LauncherHost } from './lib/state.ts';
