// The values chela-hooks-mod keeps in `$.state` (Mod 2/4, CMX-182).
export type Stamp = string | null

declare module 'claude-code' {
  interface PluginState {
    'chela-hooks-mod': { lastStamp: Stamp }
  }
}
