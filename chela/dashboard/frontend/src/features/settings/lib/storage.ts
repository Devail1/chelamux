// This browser's storage, every read and write wrapped: a private window or blocked site data
// leaves Settings working, just forgetting.
export function lsRead(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function lsSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch { /* storage blocked */ }
}

export function lsRemove(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch { /* storage blocked */ }
}

/** A stored choice, or `dflt` when it is missing or not one of `valid`. */
export function lsChoice<T extends string>(key: string, valid: readonly T[], dflt: T): T {
  const v = lsRead(key);
  return valid.includes(v as T) ? (v as T) : dflt;
}
