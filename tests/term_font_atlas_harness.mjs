// CMX-155 harness: exercises the REAL _TERM_FONT_PREF_SHIM source (chela/dashboard/
// app.py) against a faithful miniature of xterm.js's own texture-atlas cache —
// `acquireTextureAtlas` / `configEquals` in xterm@5.3.0's
// browser/renderer/shared/CharAtlasCache.ts + CharAtlasUtils.ts (fetched and read
// verbatim while diagnosing this bug; reproduced here restricted to the two config
// fields that matter, fontFamily + fontSize — both are real fields in configEquals).
//
// The real algorithm: a terminal only SHEDS a texture atlas it owns when a render
// runs against a config that no longer matches (`configEquals` false) — the old
// entry is dropped (disposed if this terminal was its sole owner) and a fresh one
// is built. Re-applying the SAME fontFamily/fontSize is a cache HIT on the exact
// entry already on file — including one baked from a fallback font at first paint,
// before the real webfont was ready. That is CMX-155: the reported tofu-forever
// bug. xterm.js's own onMultipleOptionChange(['fontFamily','fontSize',...])
// listener (RenderService.ts) runs this eviction SYNCHRONOUSLY on assignment (not
// debounced — that path is reserved for row repaints), which this harness models
// by running `acquireTextureAtlas` synchronously from the fake `t.options` setters.
//
// window.term here never owns `clearTextureAtlas` unless the harness is invoked
// with mode "with-clear" — CMX-155 was reported on a build where it is either
// absent or a no-op, so the fix under test must not depend on it.
//
// Usage: node term_font_atlas_harness.mjs <shimJsPath> <mode> <prefsJson>
//   mode: "no-clear" | "with-clear"
//   prefsJson: JSON object for localStorage, e.g. {} for "no custom prefs" (the
//     exact scenario that trips the old early-return: target already matches
//     what ttyd painted at mount).
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const [, , shimPath, mode, prefsJson] = process.argv;
const shimSrc = readFileSync(shimPath, 'utf8');
const prefs = JSON.parse(prefsJson || '{}');

// --- miniature of xterm's real CharAtlasCache (see file header) ---
const charAtlasCache = []; // { config: {fontFamily, fontSize}, atlas, ownedBy: [term] }
function configEquals(a, b) {
  return a.fontFamily === b.fontFamily && a.fontSize === b.fontSize;
}
let fontsReady = false;
function acquireTextureAtlas(term, config) {
  for (let i = 0; i < charAtlasCache.length; i++) {
    const entry = charAtlasCache[i];
    const idx = entry.ownedBy.indexOf(term);
    if (idx >= 0) {
      if (configEquals(entry.config, config)) return entry.atlas;
      if (entry.ownedBy.length === 1) charAtlasCache.splice(i, 1);
      else entry.ownedBy.splice(idx, 1);
      break;
    }
  }
  for (const entry of charAtlasCache) {
    if (configEquals(entry.config, config)) { entry.ownedBy.push(term); return entry.atlas; }
  }
  const atlas = { tofu: !fontsReady, config: { ...config } };
  charAtlasCache.push({ config, atlas, ownedBy: [term] });
  return atlas;
}

// --- fake window.term ---
let currentAtlas;
const rawOptions = { fontFamily: '', fontSize: 0 };
function onGlyphOptionChange() {
  currentAtlas = acquireTextureAtlas(term, { fontFamily: rawOptions.fontFamily, fontSize: rawOptions.fontSize });
}
const term = {
  rows: 24,
  options: {
    get fontFamily() { return rawOptions.fontFamily; },
    set fontFamily(v) { rawOptions.fontFamily = v; onGlyphOptionChange(); },
    get fontSize() { return rawOptions.fontSize; },
    set fontSize(v) { rawOptions.fontSize = v; onGlyphOptionChange(); },
  },
  fit() {},
  refresh() {},
};
if (mode === 'with-clear') {
  // A working clearTextureAtlas: evict whatever this terminal currently owns,
  // then force the immediate re-acquire real xterm gets from the fullRefresh()
  // RenderService.clearTextureAtlas() triggers (this harness treats rendering as
  // synchronous throughout, same as the glyph-option-change path above).
  term.clearTextureAtlas = function () {
    for (let i = 0; i < charAtlasCache.length; i++) {
      const idx = charAtlasCache[i].ownedBy.indexOf(term);
      if (idx >= 0) { charAtlasCache[i].ownedBy.splice(idx, 1); if (charAtlasCache[i].ownedBy.length === 0) charAtlasCache.splice(i, 1); break; }
    }
    onGlyphOptionChange();
  };
} else if (mode !== 'no-clear') {
  throw new Error(`unknown mode ${mode}`);
}

// --- simulate ttyd's initial synchronous paint, BEFORE the webfont is ready ---
const DEFAULT_FAM = "'JetBrains Mono','Symbols Nerd Font','Miriam Mono CLM',monospace";
const DEFAULT_SIZE = 14;
term.options.fontFamily = DEFAULT_FAM;
term.options.fontSize = DEFAULT_SIZE;
// The bug report's diagnosed state: fonts ARE fully loaded by the time the shim's
// document.fonts.load() promise resolves — this is not a load-order race, the
// atlas is just stale despite that.
fontsReady = true;

// --- fake browser globals the shim needs ---
// Real browsers have `window === globalThis`, so a bare `requestAnimationFrame(x)`
// call inside the shim and a `window.requestAnimationFrame(x)` call are the SAME
// lookup. The sandbox has to mirror that (not just hand `window` a sibling
// property) or the shim's bare `requestAnimationFrame`/`setInterval` calls would
// throw ReferenceError in here despite working fine in a real tab.
const localStorage = { getItem: (k) => (k in prefs ? prefs[k] : null) };
const listeners = { visibilitychange: [], storage: [] };
const document = {
  hidden: false,
  fonts: { load: () => Promise.resolve() },
  addEventListener: (name, cb) => { (listeners[name] ||= []).push(cb); },
};
const sandbox = {
  document,
  localStorage,
  term,
  setTimeout, setInterval, clearInterval, setImmediate, console,
  requestAnimationFrame: (cb) => setImmediate(cb),
  addEventListener: (name, cb) => { (listeners[name] ||= []).push(cb); },
};
sandbox.window = sandbox;

const ctx = vm.createContext(sandbox);
vm.runInContext(shimSrc, ctx, { filename: 'term-font-pref-shim.js' });

// Drive one interval tick's worth of work: the shim's own setInterval already
// fired once synchronously isn't guaranteed, so invoke the exposed hook directly
// (this is exactly what the parent frame does for "instant feedback").
ctx.chelaApplyTermPrefs();

// Let every promise microtask + our fake rAF (setImmediate) macrotask settle.
function settle(rounds) {
  return rounds <= 0 ? Promise.resolve() : new Promise((res) => setImmediate(res)).then(() => settle(rounds - 1));
}
await settle(20);

process.stdout.write(JSON.stringify({
  tofu: !!currentAtlas?.tofu,
  fontFamily: rawOptions.fontFamily,
  fontSize: rawOptions.fontSize,
  cacheSize: charAtlasCache.length,
}));
// The shim's own setInterval (real setInterval, deliberately) would otherwise
// keep this process alive up to 60 ticks * 500ms — this harness only needs one
// reconciliation pass.
process.exit(0);
