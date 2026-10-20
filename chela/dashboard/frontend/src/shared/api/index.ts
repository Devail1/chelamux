// CMX-217 (CMX-212 P03): the page's HTTP client and its boot config. index.html renders the
// config as one JSON block (#chela-boot) instead of three inline window globals.

export interface BootConfig {
  /** app.py ASSET_VERSION: the deploy this page was rendered by (CMX-426). */
  assetVersion: string;
  /** CHELA_TERMINALS_ENABLED: false means the template drew no Wall. */
  terminalsEnabled: boolean;
  /** CHELA_WALL_TILE_DISPATCHED: a dispatched worker opens as a full tile, not minimized. */
  wallTileDispatched: boolean;
}

/** What a page without the block runs as: the values the legacy globals defaulted to. */
export const BOOT_DEFAULTS: Readonly<BootConfig> = {
  assetVersion: '',
  terminalsEnabled: true,
  wallTileDispatched: false,
};

/** The page's boot config. A missing or unparsable block, or a field of the wrong type,
 * falls back to BOOT_DEFAULTS field by field: a typo never reads as a value. */
export function readBoot(doc: Document = document): BootConfig {
  let raw: unknown = null;
  try {
    raw = JSON.parse(doc.getElementById('chela-boot')?.textContent ?? 'null');
  } catch {
    raw = null;
  }
  const o = (raw && typeof raw === 'object' ? raw : {}) as Partial<Record<keyof BootConfig, unknown>>;
  return {
    assetVersion: typeof o.assetVersion === 'string' ? o.assetVersion : BOOT_DEFAULTS.assetVersion,
    terminalsEnabled:
      typeof o.terminalsEnabled === 'boolean' ? o.terminalsEnabled : BOOT_DEFAULTS.terminalsEnabled,
    wallTileDispatched:
      typeof o.wallTileDispatched === 'boolean' ? o.wallTileDispatched : BOOT_DEFAULTS.wallTileDispatched,
  };
}

export const BOOT = readBoot();
export const ASSET_VERSION = BOOT.assetVersion;
export const BASE_PATH = window.location.pathname.replace(/\/$/, '');

/** A static URL that carries the deploy's version, so a deploy never serves a cached copy.
 * Relative ES imports need nothing: they resolve against the versioned bundle URL. */
export function staticUrl(path: string): string {
  return `${BASE_PATH}/static/${ASSET_VERSION ? `v/${ASSET_VERSION}/` : ''}${path}`;
}

/** GET (or `opts`) `path` under the page's base path; resolves to the parsed JSON body. */
export async function api(path: string, opts?: RequestInit): Promise<unknown> {
  const res = await fetch(BASE_PATH + path, opts);
  return res.json();
}
