### Changed

- **The dashboard page is one React root.** `templates/index.html` is now its `<head>`, the
  pre-paint sidebar restore, the boot block and `<div id="root">`; the app shell
  (`frontend/src/features/shell`) renders the sidebar, the canvas, the phone controls, the
  safety float and the overlays, with the same classes and ids, so the look does not change.
  The view switch, the view registry and the tab title and favicon badge moved to TypeScript
  with it, and the last plain-JS modules (`nav.js`, `views.js`, `viewreg.js`, `util.js`,
  `icons.js`) are gone. `style.css` is the build entry's stylesheet, linked from the manifest.
  `window.chela` holds only what a terminal pane calls, `openPalette` and `toggleSettings`.
  (CMX-248, CMX-212 P34)
