# chela collab relay

A **dumb, opaque WebSocket fan-out relay** for chela's collaborative terminals —
a Cloudflare Worker + one Durable Object per room. It broadcasts every frame it
receives to the other sockets in the same room and **never parses or merges** the
payload; all Yjs-awareness / presence logic lives in the browser clients and the
dashboard publisher. It holds no application state and is safe to share.

chela ships pointing at a public instance, but **you should run your own** — it's
one command on Cloudflare's free tier (SQLite-backed Durable Objects are included).

## Deploy your own

```sh
cd chela/collab-relay
npm install          # first time only (installs wrangler)
npx wrangler login   # first time only
npm run deploy       # === wrangler deploy
```

Wrangler prints the deployed URL, e.g. `https://chela-collab-relay.<you>.workers.dev`.
Point chela at it via the `wss://` form:

```sh
export CHELA_COLLAB_RELAY="wss://chela-collab-relay.<you>.workers.dev"
# then restart the dashboard (pm2 restart chela-dashboard, or however you run it)
```

Both the browser client (`presence.js`, via an injected config) and the
server-side publisher (`chela/collab.py`) read `CHELA_COLLAB_RELAY`, so they stay
in sync automatically.

## Rooms & privacy

Rooms are namespaced per chela instance: `room = <instance-secret>-<wid>`, where
the secret is a random id persisted in `~/.chela/collab_id`. That keeps different
instances on a shared relay from colliding on (or guessing) each other's
wid-keyed rooms.

**This is namespacing, not encryption.** Presence frames (who's here, cursor
positions) travel the relay in the clear, so a party that learns your room id can
observe presence. End-to-end encryption / capability tokens are a deliberate
later step. Run your own relay for anything beyond a demo.

## What's here

- `src/index.js` — the Worker: a `Room` Durable Object that accepts hibernatable
  WebSockets and rebroadcasts each frame to the room's other sockets.
- `wrangler.jsonc` — Worker + DO binding + the SQLite-DO migration (free tier).
