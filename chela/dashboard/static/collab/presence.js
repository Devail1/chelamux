// chela collaborative-terminal presence — a Yjs-awareness overlay over the dumb
// CF relay. Injected into every ttyd page by app.py; SELF-GATES purely on the
// server "shared" flag (the host clicked Share; per-wid, in-memory), so normal
// wall panes are unaffected. The server flag is the SOLE gate: un-sharing or a
// dashboard restart (which clears the in-memory flag) truly revokes the link —
// there is no client-side ?collab bypass the host can't revoke. Everything here
// is client-side; the relay only forwards opaque frames. Config (relay, room
// prefix, grid, shared) arrives via window.__CHELA_COLLAB__.

import { computeFit } from './fit.js';

const CFG = window.__CHELA_COLLAB__ || {};
if (CFG.shared) {
  const RELAY = CFG.relay || 'wss://chela-collab-relay.liav-acc.workers.dev';
  const wid = decodeURIComponent((location.pathname.match(/\/term\/([^/]+)/) || [, 'default'])[1]);
  // Instance-namespaced, relay-safe room id — MUST mirror chela/collab.py
  // room_id(): sanitize("<prefix>-<wid>"). The prefix stops instances on a shared
  // relay from colliding on (and guessing) the same wid-keyed rooms. The relay
  // routes on the raw path segment ([\w@.\-]+), so we keep '@'/'-' and only
  // replace genuinely-unsafe chars — never percent-encode (%40 misses the route).
  const room = ((CFG.prefix || 'default') + '-' + wid).replace(/[^\w@.\-]/g, '_');

  // Same Yjs across the Doc and the awareness protocol (?deps pins it).
  const Y = await import('https://esm.sh/yjs@13.6.20');
  const { Awareness, encodeAwarenessUpdate, applyAwarenessUpdate, removeAwarenessStates } =
    await import('https://esm.sh/y-protocols@1.0.6/awareness?deps=yjs@13.6.20');

  const NAMES = ['Fox', 'Owl', 'Wolf', 'Bear', 'Hawk', 'Lynx', 'Otter', 'Crane'];
  const COLORS = ['#f97583', '#58a6ff', '#3fb950', '#d29922', '#bc8cff', '#39c5cf', '#ff9e64', '#e06c9f'];
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const lsGet = (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } };
  // Stable colour derived from the name (hash) — a given name always looks the
  // same and never rerolls; the initial + hue give a non-hue-only cue.
  const colorFor = (s) => {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return COLORS[Math.abs(h) % COLORS.length];
  };
  // Auto-name is generated ONCE and persisted (it used to reroll every page load,
  // breaking presence continuity); a user-set name (Settings → Collaboration)
  // overrides it, live via the storage listener below.
  let autoName = lsGet('chela_collab_autoname');
  if (!autoName) {
    autoName = NAMES[Math.floor(Math.random() * NAMES.length)] + '-' + Math.floor(Math.random() * 90 + 10);
    lsSet('chela_collab_autoname', autoName);
  }
  const myName = () => (lsGet('chela_collab_name') || autoName);
  const me = { name: myName(), color: colorFor(myName()) };

  const doc = new Y.Doc();
  const awareness = new Awareness(doc);
  awareness.setLocalStateField('user', me);
  awareness.setLocalStateField('cursor', null);

  // --- transport: dumb relay, binary awareness frames ------------------------
  let ws;
  const broadcast = () => {
    if (ws && ws.readyState === 1) ws.send(encodeAwarenessUpdate(awareness, [doc.clientID]));
  };
  const connect = () => {
    ws = new WebSocket(RELAY + '/room/' + room);
    ws.binaryType = 'arraybuffer';
    ws.onopen = broadcast;
    ws.onmessage = (ev) => {
      try { applyAwarenessUpdate(awareness, new Uint8Array(ev.data), 'remote'); } catch (_) {}
    };
    ws.onclose = () => setTimeout(connect, 1500); // spike: naive reconnect
  };
  connect();
  // Broadcast our own state on any local change (not when we just applied a remote one).
  awareness.on('update', (_, origin) => { if (origin !== 'remote') broadcast(); });
  // Heartbeat: a late joiner learns about everyone already present within ~4s.
  setInterval(broadcast, 4000);
  addEventListener('beforeunload', () => {
    removeAwarenessStates(awareness, [doc.clientID], 'local');
    broadcast();
  });

  // --- local cursor: pointer as a fraction of the viewport (resolution-free) --
  let last = 0;
  addEventListener('mousemove', (e) => {
    const now = performance.now();
    if (now - last < 40) return; // ~25fps
    last = now;
    awareness.setLocalStateField('cursor', { x: e.clientX / innerWidth, y: e.clientY / innerHeight });
  });

  // --- overlay UI ------------------------------------------------------------
  const layer = document.createElement('div');
  layer.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483000';
  document.body.appendChild(layer);
  const style = document.createElement('style');
  style.textContent = '.chela-pip{width:7px;height:7px;border-radius:50%;box-shadow:0 0 0 1px rgba(0,0,0,.35)}'
    + '.chela-pip-live{animation:chelaPulse 1.2s ease-in-out infinite}'
    + '@keyframes chelaPulse{0%,100%{opacity:1}50%{opacity:.3}}'
    // Shared-view backdrop + framed grid (§5.1). Centering is interaction-safe:
    // flex only repositions .xterm within its own rect, so xterm's mouse→cell
    // math (relative to its bounding rect) still holds.
    // Pin the backdrop to the full viewport (position:fixed;inset:0 + explicit
    // 100vw/100vh) so making it a flex container can NOT shrink-wrap it to its
    // content — that collapse is what fed the shrink spiral. overflow:hidden
    // keeps a stray scrollbar from perturbing innerWidth mid-fit.
    + 'body.chela-shared{position:fixed;inset:0;width:100vw;height:100vh;margin:0;'
    + 'box-sizing:border-box;overflow:hidden;display:flex;align-items:center;'
    + 'justify-content:center;background:radial-gradient(1200px 620px at 50% 32%,#151a23,#0a0c11)}'
    + '.chela-framed{box-shadow:0 0 0 1px rgba(255,255,255,.07),0 12px 48px rgba(0,0,0,.55);'
    + 'border-radius:6px}';
  document.head.appendChild(style);
  const pills = document.createElement('div');
  pills.style.cssText = 'position:fixed;top:8px;right:10px;display:flex;gap:6px;flex-wrap:wrap;'
    + 'justify-content:flex-end;max-width:60vw;font:600 11px/1 system-ui,sans-serif';
  layer.appendChild(pills);
  const cursors = new Map();

  // Embedded in the dashboard iframe → the parent exposes chelaPresence, so we
  // surface presence in the pane HEADER (a facepile) instead of in-iframe pills
  // that would overlap terminal content. Standalone joiner → keep the pills.
  const hooked = () => {
    try { return window.parent && window.parent !== window && typeof window.parent.chelaPresence === 'function'; }
    catch (_) { return false; }
  };
  const presenceData = () => {
    const humans = []; let agent = null;
    for (const [id, st] of awareness.getStates()) {
      if (!st.user) continue;
      if (st.user.bot) agent = { name: st.user.name, color: st.user.color, status: st.user.status };
      else humans.push({ name: st.user.name, color: st.user.color, you: id === doc.clientID });
    }
    return { humans, agent, count: humans.length };
  };

  const render = () => {
    const states = awareness.getStates(); // clientID -> {user, cursor}
    if (hooked()) {
      pills.style.display = 'none';
      try { window.parent.chelaPresence(wid, presenceData()); } catch (_) { /* cross-frame */ }
    } else {
    pills.style.display = '';
    pills.innerHTML = '';
    for (const [id, st] of states) {
      if (!st.user) continue;
      const u = st.user;
      const pill = document.createElement('div');
      if (u.bot) {
        // Agent-as-peer: a running Claude, published server-side. Gear glyph +
        // a live status pip (green busy / amber waiting / grey idle), ringed so
        // it reads as non-human at a glance.
        const pip = u.status === 'waiting' ? '#d29922' : u.status === 'busy' ? '#3fb950' : '#8b949e';
        pill.innerHTML = '<span style="opacity:.85">⚙</span>&nbsp;' + esc(u.name)
          + '<span class="chela-pip' + (u.status === 'busy' ? ' chela-pip-live' : '')
          + '" style="background:' + pip + '"></span>';
        pill.style.cssText = 'display:inline-flex;align-items:center;gap:5px;padding:3px 9px;'
          + 'border-radius:999px;color:#fff;box-shadow:0 1px 4px rgba(0,0,0,.45);'
          + 'outline:1.5px solid rgba(255,255,255,.5);outline-offset:1px;background:' + u.color;
      } else {
        pill.textContent = id === doc.clientID ? u.name + ' (you)' : u.name;
        pill.style.cssText = 'padding:3px 9px;border-radius:999px;color:#fff;'
          + 'box-shadow:0 1px 4px rgba(0,0,0,.45);background:' + u.color;
      }
      pills.appendChild(pill);
    }
    }
    const live = new Set();
    for (const [id, st] of states) {
      if (id === doc.clientID || !st.cursor || !st.user) continue;
      live.add(id);
      let el = cursors.get(id);
      if (!el) {
        el = document.createElement('div');
        el.style.cssText = 'position:fixed;transform:translate(-2px,-2px);'
          + 'transition:left .06s linear,top .06s linear;will-change:left,top';
        el.innerHTML = '<svg width="18" height="18" viewBox="0 0 18 18">'
          + '<path d="M2 2 L2 15 L6 11 L9 17 L11 16 L8 10 L14 10 Z" fill="' + st.user.color
          + '" stroke="#0009" stroke-width=".7"/></svg>'
          + '<span style="position:absolute;left:15px;top:11px;padding:1px 5px;border-radius:4px;'
          + 'font:600 10px system-ui;color:#fff;white-space:nowrap;background:' + st.user.color
          + '">' + st.user.name + '</span>';
        layer.appendChild(el);
        cursors.set(id, el);
      }
      el.style.left = (st.cursor.x * innerWidth) + 'px';
      el.style.top = (st.cursor.y * innerHeight) + 'px';
    }
    for (const [id, el] of cursors) {
      if (!live.has(id)) { el.remove(); cursors.delete(id); }
    }
  };

  // --- presenter grid --------------------------------------------------------
  // "Presenter" model (§5 item 2): the MASTER — the host, i.e. the embedded
  // (hooked) peer that shared the pane — defines the grid = its own live pane
  // dims. So the terminal FILLS the master's pane (no letterbox → no window-in-
  // window), while joiners (raw-link peers) pin to the master's dims and letterbox
  // to fit. The master publishes its dims via Yjs awareness; joiners adopt them
  // live and on master resize. The tmux window is pinned to the master's dims so
  // the one shared PTY is master-sized; each client sizes the FONT to fit.
  // Fallback if the master leaves: last-seen awareness dims → injected config →
  // 120x30. Peer count is from the relay (awareness), excluding the agent bot.
  // Presenter = the host, i.e. the embedded (hooked) peer. Evaluated per tick
  // (hooked()) since the parent's chelaPresence hook may attach just after init.
  const GRID = { cols: CFG.cols || 120, rows: CFG.rows || 30 };  // injected fallback
  let lastPin = null;         // master: last dims we POSTed (avoid re-POST spam)
  let lastPub = null;         // master: last dims we published to awareness
  let lastSeenDims = null;    // joiner: last master dims seen in awareness

  const humanPeers = () => {
    let n = 0;
    for (const [, st] of awareness.getStates()) if (st.user && !st.user.bot) n++;
    return n;
  };

  // presence.js runs INSIDE the ttyd iframe, so innerWidth/innerHeight is the
  // pane's viewport, and .xterm-screen is the rendered grid (cols*cell wide).
  const gridEl = () => {
    const t = window.term;
    return t && t.element ? (t.element.querySelector('.xterm-screen') || t.element) : null;
  };

  // The presenter's natural grid: how many cols/rows of the CURRENT cell size fit
  // its viewport. Derived from the measured cell width (invariant of the pinned
  // cols), so it's correct even while pinned and tracks the master's resizes.
  const masterDims = () => {
    const t = window.term, g = gridEl();
    if (!t || !g || !t.cols || !t.rows) return null;
    const cellW = g.offsetWidth / t.cols, cellH = g.offsetHeight / t.rows;
    if (!(cellW > 0 && cellH > 0)) return null;
    return { cols: Math.max(20, Math.floor(innerWidth / cellW)),
             rows: Math.max(6, Math.floor(innerHeight / cellH)) };
  };

  // Joiner's target: the master's dims published in awareness; else last-seen,
  // else the injected config, else a sane default.
  const sharedDims = () => {
    for (const [, st] of awareness.getStates()) {
      if (st.grid && st.grid.cols > 0 && st.grid.rows > 0) { lastSeenDims = st.grid; return st.grid; }
    }
    return lastSeenDims || GRID;
  };

  const postGrid = (body) => fetch('/api/term/' + encodeURIComponent(wid) + '/grid', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).catch(() => {});

  // Letterbox-fit a TARGET grid (the master's dims) into the viewport by
  // re-rendering glyphs at a fitted fontSize — native res → sharp; no CSS scale →
  // no blur. Font px is an integer (a thin, intentional letterbox margin). The
  // size is published in window.__CHELA_GRID_FONT__ so the font-pref shim targets
  // the SAME size and skips its own t.fit(). Fit is the pure, idempotent
  // computeFit over INVARIANTS (window viewport + font cell-ratios), never the
  // element we center — that coupling caused the shrink spiral. See fit.js.
  const fitToDims = (dims) => {
    const t = window.term, g = gridEl();
    if (!t || !g || !t.options || !t.cols || !t.rows || !dims) return;
    const cur = t.options.fontSize || 14;
    const natW = g.offsetWidth, natH = g.offsetHeight;
    if (!natW || !natH) return;
    const cellWPerPx = natW / (t.cols * cur);
    const cellHPerPx = natH / (t.rows * cur);
    const next = computeFit(innerWidth, innerHeight, dims.cols, dims.rows, cellWPerPx, cellHPerPx);
    if (!next) return;
    window.__CHELA_GRID_FONT__ = next;
    if (next !== cur) {
      t.options.fontSize = next;
      if (t.clearTextureAtlas) t.clearTextureAtlas();
      if (t.refresh && t.rows) t.refresh(0, t.rows - 1);
    }
    // Force xterm to the shared grid — ttyd's own FitAddon would otherwise leave
    // it viewport-sized (cols=59) so the pin never showed. tmux is window-size
    // manual at these dims, so pushing the size client-side stays consistent.
    if (t.cols !== dims.cols || t.rows !== dims.rows) {
      try { t.resize(dims.cols, dims.rows); } catch (_) { /* xterm not ready */ }
    }
    // §5.1 framing: centered on a styled backdrop (classes, not a transform on
    // the interactive grid, which would desync xterm's mouse→cell mapping).
    document.body.classList.add('chela-shared');
    g.classList.add('chela-framed');
  };

  const clearFit = () => {
    window.__CHELA_GRID_FONT__ = null;
    const g = gridEl();
    if (g) { g.style.transform = 'none'; g.classList.remove('chela-framed'); }
    document.body.classList.remove('chela-shared');
    // hand sizing back to the font-pref shim: restores the user's px + re-fits.
    if (window.chelaApplyTermPrefs) window.chelaApplyTermPrefs();
    else { const t = window.term; if (t && t.fit) { try { t.fit(); } catch (_) {} } }
  };

  const applyGrid = () => {
    const multi = humanPeers() >= 2;
    if (hooked()) {
      // Presenter: our pane IS the grid. Publish dims for joiners; when others are
      // present, pin the window to our dims and keep our own font (fills our pane,
      // no letterbox). Alone → dynamic.
      const dims = masterDims();
      if (dims && (!lastPub || lastPub.cols !== dims.cols || lastPub.rows !== dims.rows)) {
        lastPub = dims;                              // only on change; the 4s
        awareness.setLocalStateField('grid', dims);  // heartbeat covers late joiners
      }
      if (multi && dims) {
        if (!lastPin || lastPin.cols !== dims.cols || lastPin.rows !== dims.rows) {
          lastPin = dims;
          postGrid({ cols: dims.cols, rows: dims.rows });
        }
        const t = window.term;
        if (t && (t.cols !== dims.cols || t.rows !== dims.rows)) {
          try { t.resize(dims.cols, dims.rows); } catch (_) { /* not ready */ }
        }
        window.__CHELA_GRID_FONT__ = null;               // keep our preferred font
        document.body.classList.remove('chela-shared');  // fills → no backdrop
        const g = gridEl(); if (g) g.classList.remove('chela-framed');
      } else if (lastPin) {
        lastPin = null;
        postGrid({ peers: 1 });                          // unpin → dynamic
        clearFit();
      }
      return;
    }
    // Joiner: letterbox-fit to the master's dims (always — a joiner is in a shared
    // session even if momentarily the only peer while the master reconnects).
    fitToDims(sharedDims());
  };

  awareness.on('change', () => { render(); applyGrid(); });
  addEventListener('resize', applyGrid);   // master re-derives dims; joiner refits
  // Live display-name updates (Settings → Collaboration writes chela_collab_name;
  // same-origin storage event, like the font-pref shim). Re-broadcast our state.
  addEventListener('storage', (e) => {
    if (e.key && e.key !== 'chela_collab_name' && e.key !== 'chela_collab_autoname') return;
    me.name = myName(); me.color = colorFor(me.name);
    awareness.setLocalStateField('user', { name: me.name, color: me.color });
  });
  // window.term may not exist yet, and ttyd/the font shim keep re-fitting for
  // ~30s; a light interval keeps the letterbox applied while collaborating and
  // reconciles the PTY-pin round-trip (POST → tmux resize → xterm resize).
  setInterval(applyGrid, 800);

  render();
  applyGrid();
  console.log('[chela-collab] presence active — room', room, 'as', me.name, '| grid', GRID.cols + 'x' + GRID.rows);
}
