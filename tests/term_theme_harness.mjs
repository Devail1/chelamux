// CMX-381 harness: runs the REAL terminal-theme shim (app.py `_term_theme_shim`,
// extracted from the page `/term/<wid>/` actually serves) against a fake ttyd page:
// a same-origin localStorage, a documentElement that records CSS vars, and a fake
// xterm `window.term` whose `options.theme` is a plain settable field (xterm.js's
// own `terminal.options.theme = {...}` contract).
//
// It replays the lifecycle a pane goes through and prints what the terminal was
// painted with at each step:
//   head          CSS vars set synchronously while <head> parses (before xterm)
//   onAssign      ttyd assigns window.term — theme at that instant
//   afterReapply  ttyd re-applies its LAUNCH theme (WS connect), then one frame
//   afterLive     localStorage chela_theme := <liveTo>, parent pokes chelaApplyTermTheme()
//   afterStorage  localStorage chela_theme := <storageTo>, a `storage` event fires
//
// Usage: node term_theme_harness.mjs <shimJsPath> <scenarioJson>
//   scenario: {store: {chela_theme: ...}, launch: {<ITheme>}, liveTo, storageTo}
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const [, , shimPath, scenarioJson] = process.argv;
const shimSrc = readFileSync(shimPath, 'utf8');
const sc = JSON.parse(scenarioJson || '{}');
const store = { ...(sc.store || {}) };

const localStorage = {
    getItem: k => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
};
const cssVars = {};
const document = { documentElement: { style: { setProperty: (k, v) => { cssVars[k] = v; } } } };
let rafQ = [];
const listeners = {};
const win = {
    requestAnimationFrame: cb => { rafQ.push(cb); return rafQ.length; },
    addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); },
};
const ctx = vm.createContext({
    window: win, localStorage, document,
    requestAnimationFrame: win.requestAnimationFrame,
    setInterval: () => 0, setTimeout: () => 0, clearInterval: () => {},
});
vm.runInContext(shimSrc, ctx);

function frame() { const q = rafQ; rafQ = []; q.forEach(cb => cb()); }
const snap = t => (t && t.options && t.options.theme ? { ...t.options.theme } : null);

const out = { head: { ...cssVars } };
const launch = sc.launch || {};
const term = { options: { theme: { ...launch } } };
win.term = term;                         // ttyd mounts the terminal
out.onAssign = snap(term);
out.sameTermBack = win.term === term;    // the setter hook must not swallow it

term.options.theme = { ...launch };      // ttyd re-applies launch client-options
frame();
out.afterReapply = snap(term);

if (sc.liveTo) {
    store.chela_theme = sc.liveTo;
    if (typeof win.chelaApplyTermTheme === 'function') win.chelaApplyTermTheme();
    out.afterLive = snap(term);
    out.cssAfterLive = { ...cssVars };
}
if (sc.storageTo) {
    store.chela_theme = sc.storageTo;
    (listeners.storage || []).forEach(f => f({ key: 'chela_theme' }));
    out.afterStorage = snap(term);
}
process.stdout.write(JSON.stringify(out));
