// The dashboard's icon vocabulary (CMX-199): a MEANING name -> a Lucide icon name.
// A meaning is chosen here once, and every surface draws it through `icon()` in
// util.js. Pure data, no imports, so the React port's icons.ts (CMX-212 P04) takes
// the same table unchanged and swaps only the renderer (lucide-react).
//
// Each entry replaces an emoji the legacy templates used (the glyph is noted, so a
// conversion can find its meaning). Colour comes from the surface's state class via
// `currentColor`; the shape still differs per state, so hue is never the only cue.
const ICONS = Object.freeze({
    judge: 'scale',                 // ⚖ judging, testing, pending approvals
    warning: 'triangle-alert',      // ⚠
    ok: 'check',                    // ✓ done, ci green, PR open
    done: 'circle-check',           // ✅ run done (toast)
    close: 'x',                     // ✕ close a dialog
    failed: 'x',                    // ✗ / ✕ failed, ci red, died, denied
    blocked: 'ban',                 // ⛔ can't be approved, blocked
    needsHuman: 'octagon-x',        // 🛑 needs a human, rework cap reached
    accessRequest: 'hand',          // 🙋
    viewOnly: 'eye',                // 👁 share mode: watch only
    typing: 'keyboard',             // ⌨ share mode: guest may type
    heldOut: 'eye-off',             // 🙈 held-out guards
    review: 'search',               // 🔍 / 👀 awaiting review
    rework: 'repeat',               // 🔁 changes requested, rework count
    running: 'play',                // ▶
    wired: 'plug',                  // 🔌 wire room
    parked: 'lock',                 // 🔒
    blockedRace: 'snowflake',       // 🧊
    handoff: 'notebook-pen',        // 📝 the agent's handoff note
    progress: 'square-check',       // ☑ task progress chip
    branch: 'git-branch',           // ⎇
    flag: 'flag',                   // ⚑
    web: 'globe',                   // 🌐 sandbox web access
    server: 'server',               // ⚙ a server-type session
    external: 'external-link',      // ↗
});

export { ICONS };
