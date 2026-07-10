// chela owner-presence parent client — the DASHBOARD half of the owner's presence
// surface. Makes the owner (viewing a shared pane in the wall) a first-class peer
// on the SAME T_PRESENCE protocol + relay room as the joiner SPA, so the owner's
// cursor shows to joiners as a labeled host pointer and joiners' cursors + a
// facepile render on the owner's dashboard.
//
// This module holds the pairing SECRET (from the owner-only /share-info) and owns
// the crypto (PresenceSession) + relay socket. The in-iframe shim (presence-shim.js)
// NEVER sees the secret — only normalized coordinates cross the postMessage bridge:
//   shim → parent:  {source:'chela-presence', type:'cursor'|'ready', wid, x, y}
//   parent → shim:  {source:'chela-presence-parent', type:'peers'|'stop', wid, peers}
// The parent seals the owner's cursor, opens peers, renders the facepile in the
// pane header, and pushes the peer list down for the shim to draw (it alone knows
// its exact screen rect).
//
// e2e.js + presence-core.js are byte-identical copies of the relay originals
// (tests/test_presence_sync.py guards the copy); the terminal streams over the
// relay opaquely, so we route ONLY T_PRESENCE frames and ignore the rest.

import { PresenceSession, secretFromCode, T_PRESENCE } from './e2e.js';
import {
  identity, autoName, colorForId, PeerStore, renderFacepile,
  PRESENCE_TIMEOUT_MS, HEARTBEAT_MS,
} from './presence-core.js';

const BASE_PATH = location.pathname.replace(/\/$/, '');
const ORIGIN = location.origin;
const enc = new TextEncoder(), dec = new TextDecoder();
const cssEsc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&'));

// wid -> live Session (below). Only ever touched on the main thread.
const sessions = new Map();
// wids we've asked /share-info for and got no code (avoid refetch storms on the
// shim's periodic 'ready' re-announce).
const noShare = new Set();

// Derive the relay WS url + room from the owner-only join_url
// (https://<relay>/j/<room>  ->  wss://<relay>/room/<room>).
function parseJoin(joinUrl) {
  const u = new URL(joinUrl);
  const room = decodeURIComponent(u.pathname.replace(/^.*\/j\//, ''));
  const wsProto = u.protocol === 'https:' ? 'wss:' : 'ws:';
  return { room, wsUrl: `${wsProto}//${u.host}/room/${room}` };
}

class Session {
  constructor(wid, room, wsUrl, secret) {
    this.wid = wid;
    this.room = room;
    this.wsUrl = wsUrl;
    this.secret = secret;
    this.me = identity(room);   // { peerId (persisted per-room), name (may be '') }
    this.peers = new PeerStore();
    this.ws = null;
    this.presence = null;       // PresenceSession
    this.iframeWin = null;      // the shim's contentWindow (relinked on each 'ready')
    this.lastCursor = null;
    this.backoff = 500;
    this.closed = false;
    this._sendChain = Promise.resolve();
    this._recvChain = Promise.resolve();
    this._hb = 0;
    this._prune = 0;
  }

  myName() {
    // Reuse the legacy Settings→Collaboration display name if set, else an auto
    // adjective-animal. host:true is asserted below (the ★ badge marks the owner).
    let nm = this.me.name;
    if (!nm) { try { nm = localStorage.getItem('chela_collab_name') || ''; } catch (_) {} }
    return nm || autoName(this.me.peerId);
  }

  async start() {
    this.presence = await PresenceSession.create(this.secret, this.room);
    this._connect();
    this._hb = setInterval(() => this._send(this.lastCursor ? this.lastCursor.x : null,
                                            this.lastCursor ? this.lastCursor.y : null), HEARTBEAT_MS);
    // Always repaint (not only on prune change): the pane header is rebuilt on
    // wall re-renders, wiping .gs-presence, so a steady 1s repaint keeps the
    // facepile + peer push alive even when no peer frame arrives. Cheap for small N.
    this._prune = setInterval(() => { this.peers.prune(performance.now(), PRESENCE_TIMEOUT_MS); this._render(); }, 1000);
    this._render();   // show my own avatar immediately
  }

  _connect() {
    if (this.closed) return;
    const ws = new WebSocket(this.wsUrl);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    ws.onopen = () => { this.backoff = 500; this._send(this.lastCursor ? this.lastCursor.x : null,
                                                       this.lastCursor ? this.lastCursor.y : null); };
    ws.onmessage = (ev) => {
      const env = new Uint8Array(ev.data);
      // Route by the cleartext type byte: the room also carries the (opaque to us)
      // encrypted terminal stream + joiner input — only presence is ours.
      if (env.length >= 2 && env[1] === T_PRESENCE) {
        this._recvChain = this._recvChain.then(() => this._open(env)).catch(() => {});
      }
    };
    ws.onclose = () => { if (!this.closed) { setTimeout(() => this._connect(), this.backoff); this.backoff = Math.min(this.backoff * 2, 8000); } };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }

  // Seal + send the owner cursor as a host presence frame. Serialised so the
  // per-stream seq stays strictly increasing on the wire.
  _send(x, y) {
    this.lastCursor = (x == null ? null : { x, y });
    const bytes = enc.encode(JSON.stringify({ id: this.me.peerId, name: this.myName(), host: true, x, y }));
    this._sendChain = this._sendChain.then(async () => {
      if (this.closed || !this.presence || !this.ws || this.ws.readyState !== 1) return;
      this.ws.send(await this.presence.seal(T_PRESENCE, bytes));
    }).catch(() => {});
  }

  async _open(env) {
    let pt; try { [, pt] = await this.presence.open(env); } catch (_) { return; }  // wrong key / replay → drop
    try {
      const m = JSON.parse(dec.decode(pt));
      if (m.id !== this.me.peerId) { this.peers.update(m, performance.now()); this._render(); }
    } catch (_) {}
  }

  // Called by the shim (via the global message router) with the owner's grid-
  // normalized cursor (or null off-grid).
  onCursor(x, y) { this._send(x, y); }

  // Relink the shim contentWindow (the iframe reloads on share toggles; our WS
  // persists) and push the current peer list down for it to draw.
  linkIframe(win) { this.iframeWin = win; this._pushPeers(); }

  _pushPeers() {
    if (!this.iframeWin) return;
    // Peers minus ME: the owner sees their own OS cursor, so we never draw a
    // pointer for the host in their own iframe.
    const peers = this.peers.list().map((p) => ({ id: p.id, name: p.name, color: p.color, host: p.host, x: p.x, y: p.y }));
    try { this.iframeWin.postMessage({ source: 'chela-presence-parent', type: 'peers', wid: this.wid, peers }, ORIGIN); } catch (_) {}
  }

  _render() {
    const live = this.peers.list();
    // Facepile in the pane header: me first (host), then peers. presence-core's
    // renderFacepile gives initials avatars + the ★ host badge (colorblind-safe).
    const slots = document.querySelectorAll('.gs-presence[data-presence-for="' + cssEsc(this.wid) + '"]');
    slots.forEach((slot) => {
      let fp = slot.querySelector('.gs-owner-fp');
      if (!fp) { slot.textContent = ''; fp = document.createElement('div'); fp.className = 'gs-owner-fp'; slot.appendChild(fp); }
      renderFacepile(fp, [{ id: this.me.peerId, name: this.myName(), color: colorForId(this.me.peerId), host: true }, ...live]);
    });
    // Share-count badge = number of OTHER peers (joiners), mirroring the joiner
    // count the wall showed before.
    setBadge(this.wid, live.length);
    this._pushPeers();
  }

  stop() {
    this.closed = true;
    clearInterval(this._hb); clearInterval(this._prune);
    try { this.ws && this.ws.close(); } catch (_) {}
    this.ws = null; this.presence = null;
    // Clear the header facepile + badge and tell the iframe to drop peer pointers.
    document.querySelectorAll('.gs-presence[data-presence-for="' + cssEsc(this.wid) + '"]').forEach((s) => { s.textContent = ''; });
    setBadge(this.wid, 0);
    if (this.iframeWin) { try { this.iframeWin.postMessage({ source: 'chela-presence-parent', type: 'stop', wid: this.wid }, ORIGIN); } catch (_) {} }
  }
}

// Update the share-button peer-count badge without reaching into terminals.js
// internals (same DOM contract as _updateShareBtns).
function setBadge(wid, count) {
  document.querySelectorAll('.gs-share-btn[data-wid="' + cssEsc(wid) + '"] .gs-share-count').forEach((badge) => {
    if (count > 0) { badge.textContent = String(count); badge.hidden = false; }
    else { badge.hidden = true; }
  });
}

async function fetchShareInfo(wid) {
  try {
    const res = await fetch(BASE_PATH + '/api/term/' + encodeURIComponent(wid) + '/share-info');
    return (await res.json()) || {};
  } catch (_) { return {}; }
}

// Ensure a live session for a shared wid. Idempotent: returns the existing session
// or starts a new one from its owner-only join_url + pairing_code.
async function ensure(wid, joinUrl, code) {
  if (sessions.has(wid)) return sessions.get(wid);
  if (!joinUrl || !code) return null;
  let parsed, secret;
  try { parsed = parseJoin(joinUrl); secret = secretFromCode(code); }
  catch (e) { console.warn('[chela-owner-presence] bad share info for', wid, e); return null; }
  const sess = new Session(wid, parsed.room, parsed.wsUrl, secret);
  sessions.set(wid, sess);
  try { await sess.start(); } catch (e) { console.warn('[chela-owner-presence] start failed', wid, e); sessions.delete(wid); return null; }
  return sess;
}

// The shim's 'ready' arrives with no secret; fetch the owner-only code to start.
async function ensureFromShareInfo(wid) {
  if (sessions.has(wid) || noShare.has(wid)) return sessions.get(wid) || null;
  const info = await fetchShareInfo(wid);
  if (!info || !info.pairing_code) { noShare.add(wid); return null; }
  return ensure(wid, info.join_url, info.pairing_code);
}

let _wired = false;
// One global router for all shim messages. Attached once; safe to call repeatedly.
export function initOwnerPresence() {
  if (_wired) return;
  _wired = true;
  window.addEventListener('message', async (e) => {
    if (e.origin !== ORIGIN) return;
    const d = e.data;
    if (!d || d.source !== 'chela-presence' || !d.wid) return;
    if (d.type === 'ready') {
      let sess = sessions.get(d.wid);
      if (!sess) sess = await ensureFromShareInfo(d.wid);
      if (sess) sess.linkIframe(e.source);
    } else if (d.type === 'cursor') {
      const sess = sessions.get(d.wid);
      if (sess) sess.onCursor(d.x, d.y);
    }
  });
}

// Called by terminals.js when a share is minted (it already holds join_url + code)
// so presence starts without waiting for a /share-info round-trip.
export function startOwnerPresence(wid, joinUrl, code) {
  noShare.delete(wid);
  return ensure(wid, joinUrl, code);
}

// Called by terminals.js on un-share / pane kill.
export function stopOwnerPresence(wid) {
  noShare.delete(wid);
  const sess = sessions.get(wid);
  if (sess) { sess.stop(); sessions.delete(wid); }
}
