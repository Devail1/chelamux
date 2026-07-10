// chela owner-presence iframe shim — the IN-IFRAME half of the dashboard owner's
// presence surface. Injected into every ttyd page by app.py's _term_presence_shim,
// gated purely on this window's server "shared" flag (window.__CHELA_COLLAB__).
//
// SECURITY BOUNDARY: the ttyd page is a raw terminal we don't fully trust with the
// pairing secret, so NO crypto and NO relay socket live here. This shim only:
//   1. maps the owner's local pointer over the .xterm-screen GRID → normalized
//      0..1 coords → postMessage up to the parent (which seals + relays them), and
//   2. receives the decrypted peer list back from the parent → renders peer
//      pointers on an in-iframe overlay, using ITS OWN screen rect so a cursor
//      lands on the right cell regardless of the parent's / joiners' letterbox.
// Only coordinates cross the postMessage boundary — the secret stays in the parent.
//
// The parent client is chela/dashboard/static/collab/presence-owner.js. Messages
// are same-origin (the ttyd page is proxied under the dashboard origin) and origin-
// checked both ways.

import { throttle, toNorm, fromNorm, makePointer } from './presence-core.js';

const CFG = window.__CHELA_COLLAB__ || {};
// Only run for a genuinely shared pane embedded in the dashboard. A top-level
// /term/<wid>/ (no parent) has nobody to talk to; an unshared pane must stay inert
// so normal wall panes are untouched.
if (CFG.shared && window.parent && window.parent !== window) {
  const WID = CFG.wid || '';
  const ORIGIN = location.origin;
  const toParent = (msg) => { try { window.parent.postMessage(msg, ORIGIN); } catch (_) {} };

  const screenRect = () => {
    const s = document.querySelector('.xterm-screen');
    return s ? s.getBoundingClientRect() : null;
  };

  // --- owner cursor → parent (normalized against the grid, null when off-grid) --
  const sendCursor = (x, y) => toParent({ source: 'chela-presence', type: 'cursor', wid: WID, x, y });
  const onMove = throttle((cx, cy) => {
    const n = toNorm(cx, cy, screenRect());
    sendCursor(n ? n.x : null, n ? n.y : null);
  }, 45);
  window.addEventListener('pointermove', (e) => onMove(e.clientX, e.clientY));
  // Leaving the iframe viewport hides my pointer for peers (avatar stays).
  window.addEventListener('pointerleave', () => sendCursor(null, null));
  document.addEventListener('mouseleave', () => sendCursor(null, null));

  // --- peer pointers from parent → in-iframe overlay ---------------------------
  const layer = document.createElement('div');
  layer.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483000';
  const attachLayer = () => { if (!layer.isConnected && document.body) document.body.appendChild(layer); };
  attachLayer();
  const pointers = new Map();   // peerId -> makePointer() handle
  let lastPeers = [];

  const renderPeers = () => {
    const rect = screenRect();
    for (const p of lastPeers) {
      let ptr = pointers.get(p.id);
      if (!ptr) { ptr = makePointer(); layer.appendChild(ptr.el); pointers.set(p.id, ptr); }
      ptr.color(p.color); ptr.label(p.name);
      if (rect && p.x != null && p.y != null) { const q = fromNorm(p.x, p.y, rect); ptr.at(q.x, q.y); }
      else ptr.off();
    }
    const live = new Set(lastPeers.map((p) => p.id));
    for (const [id, ptr] of pointers) if (!live.has(id)) { ptr.el.remove(); pointers.delete(id); }
  };

  window.addEventListener('message', (e) => {
    if (e.origin !== ORIGIN || e.source !== window.parent) return;
    const d = e.data;
    if (!d || d.source !== 'chela-presence-parent' || d.wid !== WID) return;
    if (d.type === 'peers') { lastPeers = Array.isArray(d.peers) ? d.peers : []; attachLayer(); renderPeers(); }
    else if (d.type === 'stop') { lastPeers = []; renderPeers(); }
  });

  // Peer pointers are pinned to grid cells; on any local resize/letterbox the grid
  // rect moves, so re-place them against the fresh rect.
  window.addEventListener('resize', renderPeers);

  // Announce readiness so the parent (re)links this contentWindow — sent on load
  // AND periodically, since the parent client can attach after the iframe (share
  // toggles reload the frame; the parent WS persists across reloads).
  const announce = () => toParent({ source: 'chela-presence', type: 'ready', wid: WID });
  announce();
  setTimeout(announce, 300);
  setTimeout(announce, 1200);
}
