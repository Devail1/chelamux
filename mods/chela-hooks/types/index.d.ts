// The values chela-hooks-mod keeps in `$.state` (Mod 2/4, CMX-182; Mod 3/4, CMX-183).
export type Stamp = string | null

// One thing waiting on the human, as GET /api/needs-you lists it (`since` in epoch seconds).
export type NeedsYouItem = {
  kind: 'approval' | 'access' | 'run'
  label: string
  since: number
  url: string
}
// The last good reading and when it was taken (ms), or null for none.
export type NeedsYou = { at: number, items: NeedsYouItem[] } | null

declare module 'claude-code' {
  interface PluginState {
    'chela-hooks-mod': { lastStamp: Stamp, needsYou: NeedsYou }
  }
}
