// chela collab relay — pure opaque fan-out. See wrangler.jsonc.
//
// The relay is deliberately DUMB: it holds no application state, never inspects
// message contents, and does no merging. It only tracks "which sockets are in
// this room" and forwards each incoming frame to the others. This is the
// Mosaic "dumb relay" pattern — merge/awareness logic is entirely client-side.

export class Room {
  constructor(state, env) {
    this.state = state;
  }

  async fetch(request) {
    if (request.headers.get("Upgrade") !== "websocket") {
      return new Response("expected websocket", { status: 426 });
    }
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    // Hibernatable accept: the DO can evict from memory between messages and the
    // runtime still delivers webSocketMessage/Close — idle cost ~ $0.
    this.state.acceptWebSocket(server);
    return new Response(null, { status: 101, webSocket: client });
  }

  // Broadcast verbatim to every OTHER socket in the room. No parsing.
  webSocketMessage(ws, message) {
    for (const peer of this.state.getWebSockets()) {
      if (peer === ws) continue;
      try { peer.send(message); } catch (_) { /* peer gone; ignore */ }
    }
  }

  webSocketClose(ws) { try { ws.close(); } catch (_) {} }
  webSocketError(ws) { try { ws.close(); } catch (_) {} }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    // /room/<name> → that room's Durable Object (one DO instance per room name).
    const m = url.pathname.match(/^\/room\/([\w@.\-]+)$/);
    if (!m) {
      return new Response("chela collab relay — connect to /room/<id>", {
        status: 200,
        headers: { "content-type": "text/plain" },
      });
    }
    const id = env.ROOM.idFromName(m[1]);
    return env.ROOM.get(id).fetch(request);
  },
};
