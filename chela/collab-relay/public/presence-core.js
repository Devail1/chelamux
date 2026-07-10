// Shared presence logic for the collaborative terminal — identity, colorblind-safe
// palette, GRID-RELATIVE coordinate mapping, throttle, timeout, and DOM render
// helpers. NO crypto and NO transport here: the caller owns the PresenceSession
// (e2e.js) and the relay socket. Used by BOTH surfaces (the joiner SPA and the
// owner dashboard), so it must stay dependency-free and identical in both copies.
//
// SYNCED COPY — this file is served from two origins (relay public/ and the
// dashboard static/collab/). Keep them byte-identical; a test asserts it.
//
// Colorblind-safe (a hard requirement — the primary user is red-weak): peers are
// distinguished by the Okabe-Ito palette ordered blue/orange/sky FIRST, and — the
// real non-hue cue — every pointer carries a name LABEL and every avatar its
// INITIALS. Never rely on hue alone; the host also gets a ★ badge.

// Okabe-Ito, ordered for red-weak separation (blue/orange/sky lead); black dropped
// (invisible on the dark terminal bg).
export const OKABE_ITO = [
  '#0072B2', // blue
  '#E69F00', // orange
  '#56B4E9', // sky blue
  '#009E73', // bluish green
  '#CC79A7', // reddish purple
  '#F0E442', // yellow
  '#D55E00', // vermillion
];

// --- small utils --------------------------------------------------------------
function hash32(s) {                 // FNV-1a, stable across peers for a given id
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return h >>> 0;
}
export const colorForId = (id) => OKABE_ITO[hash32(String(id)) % OKABE_ITO.length];

// Readable text color on a given hex bg (luminance test) — so labels/avatars are
// legible on any palette color.
export function textOn(hex) {
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.6 ? '#0a0c11' : '#ffffff';
}

const ADJ = ['Swift', 'Calm', 'Bright', 'Bold', 'Keen', 'Warm', 'Cool', 'Brave', 'Wise', 'Merry', 'Quick', 'Sly'];
const ANIMAL = ['Fox', 'Owl', 'Elk', 'Hare', 'Wren', 'Lynx', 'Crane', 'Otter', 'Finch', 'Ibis', 'Moth', 'Seal'];
export const autoName = (id) => {
  const h = hash32(String(id));
  return ADJ[h % ADJ.length] + ' ' + ANIMAL[(h >>> 8) % ANIMAL.length];
};

export const initials = (name) =>
  ((name || '?').trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join('') || '?').toUpperCase();

// --- identity (persist per-room in sessionStorage) ----------------------------
const ss = {
  get(k) { try { return sessionStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, v); } catch (_) {} },
};
function rand32hex() {
  const b = new Uint8Array(4); crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
}
// Stable identity for THIS tab+room: peerId survives refresh/reconnect so the
// facepile doesn't flicker/duplicate. name is the stored override (may be blank →
// auto). Distinct from the crypto stream-id (random per connection, nonce-safety).
export function identity(room) {
  const kId = 'chela_pid_' + room;
  let peerId = ss.get(kId);
  if (!peerId) { peerId = rand32hex(); ss.set(kId, peerId); }
  return { peerId, name: ss.get('chela_pname_' + room) || '' };
}
export function saveName(room, name) { ss.set('chela_pname_' + room, name || ''); }

// --- GRID-RELATIVE coordinates (the key gotcha) -------------------------------
// Map local mouse px → 0..1 of the GRID rect (.xterm-screen), not the viewport, so
// pointers land on the same cell across differently letterboxed/scaled viewers.
export const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
export function toNorm(clientX, clientY, rect) {
  if (!rect || rect.width <= 0 || rect.height <= 0) return null;
  const x = (clientX - rect.left) / rect.width, y = (clientY - rect.top) / rect.height;
  if (x < 0 || x > 1 || y < 0 || y > 1) return null;   // off-grid → hide the pointer
  return { x, y };
}
export function fromNorm(nx, ny, rect) {
  return { x: rect.left + nx * rect.width, y: rect.top + ny * rect.height };
}

// Leading+trailing throttle (~30-60ms) so pointer sends coalesce.
export function throttle(fn, ms = 45) {
  let last = 0, timer = 0, lastArgs;
  const run = () => { last = performance.now(); timer = 0; fn(...lastArgs); };
  return (...args) => {
    lastArgs = args;
    const wait = ms - (performance.now() - last);
    if (wait <= 0) run();
    else if (!timer) timer = setTimeout(run, wait);
  };
}

// --- peer store (state + staleness) -------------------------------------------
export class PeerStore {
  constructor() { this.peers = new Map(); }   // id -> {id,name,color,host,x,y,lastSeen}
  update(msg, now) {
    if (!msg || !msg.id) return;
    const p = this.peers.get(msg.id) || { id: msg.id, color: colorForId(msg.id) };
    p.name = msg.name || p.name || autoName(msg.id);
    p.host = !!msg.host;
    p.x = (msg.x == null ? null : msg.x);        // null = off-grid → hide pointer
    p.y = (msg.y == null ? null : msg.y);
    p.lastSeen = now;
    this.peers.set(msg.id, p);
  }
  drop(id) { return this.peers.delete(id); }
  // Remove peers unseen for timeoutMs; returns true if anything changed.
  prune(now, timeoutMs) {
    let changed = false;
    for (const [id, p] of this.peers) if (now - p.lastSeen > timeoutMs) { this.peers.delete(id); changed = true; }
    return changed;
  }
  list() { return [...this.peers.values()]; }
}

export const PRESENCE_TIMEOUT_MS = 5000;   // drop a silent peer after this
export const HEARTBEAT_MS = 2500;          // keepalive even when idle

// --- render helpers -----------------------------------------------------------
// A Figma-style pointer: tinted SVG arrow + a rounded name pill. pointer-events off.
export function makePointer() {
  const el = document.createElement('div');
  el.className = 'chela-cursor';
  el.style.cssText = 'position:fixed;left:0;top:0;pointer-events:none;z-index:2147483000;'
    + 'transform:translate(-9999px,-9999px);will-change:transform;transition:transform .06s linear';
  el.innerHTML =
    '<svg width="20" height="20" viewBox="0 0 20 20" style="display:block;filter:drop-shadow(0 1px 1.5px rgba(0,0,0,.7))">'
    + '<path d="M3 2 L3 15 L7 11 L10 18 L12.5 17 L9.5 10.5 L16 10.5 Z" stroke="#0a0c11" stroke-width="1" stroke-linejoin="round"/></svg>'
    + '<span class="pl"></span>';
  const path = el.querySelector('path'), pill = el.querySelector('.pl');
  pill.style.cssText = 'position:absolute;left:16px;top:14px;font:600 11px/1.3 system-ui,-apple-system,sans-serif;'
    + 'padding:2px 7px;border-radius:7px;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,.6)';
  return {
    el,
    color(c) { path.setAttribute('fill', c); pill.style.background = c; pill.style.color = textOn(c); },
    label(t) { pill.textContent = t; },
    at(x, y) { el.style.transform = `translate(${x}px,${y}px)`; },
    off() { el.style.transform = 'translate(-9999px,-9999px)'; },
  };
}

// A facepile avatar (initials on the peer color) + optional ★ host badge.
export function makeAvatar(peer) {
  const a = document.createElement('div');
  a.title = peer.name + (peer.host ? ' (host)' : '');
  a.textContent = initials(peer.name);
  a.style.cssText = 'position:relative;display:inline-flex;align-items:center;justify-content:center;'
    + 'width:22px;height:22px;border-radius:50%;font:700 10px/1 system-ui;margin-left:-5px;'
    + `color:${textOn(peer.color)};background:${peer.color};box-shadow:0 0 0 1.5px #0d1117`;
  if (peer.host) {
    const s = document.createElement('span');
    s.textContent = '★';
    s.style.cssText = 'position:absolute;top:-5px;right:-5px;font-size:9px;color:#F0E442;'
      + 'text-shadow:0 0 2px #000';
    a.appendChild(s);
  }
  return a;
}

// Render a facepile into a container from a peer list (rebuild is cheap for small N).
export function renderFacepile(container, peers) {
  container.textContent = '';
  container.style.paddingLeft = '5px';   // offset the first negative margin
  for (const p of peers) container.appendChild(makeAvatar(p));
}
