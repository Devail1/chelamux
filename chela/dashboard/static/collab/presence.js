// chela P2 presence spike — Yjs-awareness overlay over the dumb CF relay.
//
// Injected into every ttyd page (app.py _TERM_PRESENCE_SHIM) but SELF-GATES on
// ?collab, so the normal wall panes are unaffected — open `/term/<wid>/?collab=1`
// in two browsers to see live presence. Everything here is client-side; the relay
// (chela/collab-relay) only forwards opaque frames. SPIKE quality: hardcoded relay
// URL, esm.sh imports, naive reconnect — deliberately not productionized.

if (new URLSearchParams(location.search).has('collab')) {
  const RELAY = 'wss://chela-collab-relay.liav-acc.workers.dev';
  const wid = (location.pathname.match(/\/term\/([^/]+)/) || [, 'default'])[1];

  // Same Yjs across the Doc and the awareness protocol (?deps pins it).
  const Y = await import('https://esm.sh/yjs@13.6.20');
  const { Awareness, encodeAwarenessUpdate, applyAwarenessUpdate, removeAwarenessStates } =
    await import('https://esm.sh/y-protocols@1.0.6/awareness?deps=yjs@13.6.20');

  const NAMES = ['Fox', 'Owl', 'Wolf', 'Bear', 'Hawk', 'Lynx', 'Otter', 'Crane'];
  const COLORS = ['#f97583', '#58a6ff', '#3fb950', '#d29922', '#bc8cff', '#39c5cf', '#ff9e64', '#e06c9f'];
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  const me = { name: pick(NAMES) + '-' + Math.floor(Math.random() * 90 + 10), color: pick(COLORS) };

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
    ws = new WebSocket(RELAY + '/room/' + encodeURIComponent(wid));
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
      const pill = document.createElement('div');
      pill.textContent = id === doc.clientID ? st.user.name + ' (you)' : st.user.name;
      pill.style.cssText = 'padding:3px 9px;border-radius:999px;color:#fff;'
        + 'box-shadow:0 1px 4px rgba(0,0,0,.45);background:' + st.user.color;
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
  awareness.on('change', render);
  render();
  console.log('[chela-collab] presence active — room', wid, 'as', me.name);
}
