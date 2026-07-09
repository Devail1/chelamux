// chela P2 presence spike — Yjs-awareness overlay over the dumb CF relay.
//
// Injected into every ttyd page (app.py _TERM_PRESENCE_SHIM) but SELF-GATES on
// ?collab, so the normal wall panes are unaffected — open `/term/<wid>/?collab=1`
// in two browsers to see live presence. Everything here is client-side; the relay
// (chela/collab-relay) only forwards opaque frames. SPIKE quality: hardcoded relay
// URL, esm.sh imports, naive reconnect — deliberately not productionized.

if (new URLSearchParams(location.search).has('collab')) {
  // Config is injected by app.py (_TERM_PRESENCE_SHIM) from CHELA_COLLAB_RELAY +
  // the per-instance room prefix + the grid size. Fallbacks keep the file usable
  // standalone.
  const CFG = window.__CHELA_COLLAB__ || {};
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
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  const me = { name: pick(NAMES) + '-' + Math.floor(Math.random() * 90 + 10), color: pick(COLORS) };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

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
    + '@keyframes chelaPulse{0%,100%{opacity:1}50%{opacity:.3}}';
  document.head.appendChild(style);
  const pills = document.createElement('div');
  pills.style.cssText = 'position:fixed;top:8px;right:10px;display:flex;gap:6px;flex-wrap:wrap;'
    + 'justify-content:flex-end;max-width:60vw;font:600 11px/1 system-ui,sans-serif';
  layer.appendChild(pills);
  const cursors = new Map();

  const render = () => {
    const states = awareness.getStates(); // clientID -> {user, cursor}
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

  // --- adaptive grid ---------------------------------------------------------
  // Solo (1 human peer) → the pane fits the viewport dynamically, as usual.
  // 2+ human peers → the server pins this window to a fixed shared grid
  // (window-size manual, CHELA_TERM_COLS x ROWS) so everyone sees the identical,
  // COMPLETE grid, and we letterbox-scale it to each viewer's viewport. Keyed on
  // the room's peer count from the relay (awareness) — NOT tmux's client count,
  // and excluding the agent (bot) peer which has no viewport.
  const GRID = { cols: CFG.cols || 120, rows: CFG.rows || 30 };
  let fixed = null; // null=unknown, true=fixed/collab, false=dynamic/solo

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

  // Fill the fixed grid to the viewport by RE-RENDERING glyphs at a fitted font
  // size — the terminal draws at native resolution (honouring devicePixelRatio),
  // so text stays sharp. We do NOT CSS-scale any element: transform: scale()
  // resamples the xterm canvas bitmap → blur, worst off DPR=1. Font px is an
  // integer, so we accept a thin letterbox margin over an exact-fill blur.
  //
  // The size is published in window.__CHELA_GRID_FONT__ so the font-pref shim
  // targets the SAME size (instead of resetting it) and skips its own t.fit()
  // while we own sizing. Fitting is stable: measuring at the current size and
  // setting floor(cur * min(vw/natW, vh/natH)) converges in one step.
  const fitFont = () => {
    const t = window.term;
    const g = gridEl();
    if (!t || !g || !t.options) return;
    const natW = g.offsetWidth, natH = g.offsetHeight;
    if (!natW || !natH || !innerWidth || !innerHeight) return;
    const cur = t.options.fontSize || 14;
    const k = Math.min(innerWidth / natW, innerHeight / natH);
    const next = Math.max(6, Math.floor(cur * k));
    window.__CHELA_GRID_FONT__ = next;
    if (next !== cur) {
      t.options.fontSize = next;
      if (t.clearTextureAtlas) t.clearTextureAtlas();
      if (t.refresh && t.rows) t.refresh(0, t.rows - 1);
    }
  };

  const clearFit = () => {
    window.__CHELA_GRID_FONT__ = null;
    const g = gridEl();
    if (g) g.style.transform = 'none';   // clear any stale transform from older builds
    // hand sizing back to the font-pref shim: restores the user's px + re-fits.
    if (window.chelaApplyTermPrefs) window.chelaApplyTermPrefs();
    else { const t = window.term; if (t && t.fit) { try { t.fit(); } catch (_) {} } }
  };

  const applyGrid = () => {
    const multi = humanPeers() >= 2;
    if (multi !== fixed) {
      fixed = multi;
      fetch('/api/term/' + encodeURIComponent(wid) + '/grid', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ peers: multi ? 2 : 1 }),
      }).catch(() => {});
      if (!multi) { clearFit(); return; }
    }
    if (fixed) fitFont();
  };

  awareness.on('change', () => { render(); applyGrid(); });
  addEventListener('resize', () => { if (fixed) fitFont(); });
  // window.term may not exist yet, and ttyd/the font shim keep re-fitting for
  // ~30s; a light interval keeps the letterbox applied while collaborating and
  // reconciles the PTY-pin round-trip (POST → tmux resize → xterm resize).
  setInterval(applyGrid, 800);

  render();
  applyGrid();
  console.log('[chela-collab] presence active — room', room, 'as', me.name, '| grid', GRID.cols + 'x' + GRID.rows);
}
