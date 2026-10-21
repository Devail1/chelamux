// CMX-240 (CMX-212 P25): the types of presence-owner.js, which stays a plain ES module (it
// holds the pairing secret and the crypto, byte-shared with the joiner page). The React share
// code loads it lazily, from its versioned static URL, and reads it through these.

/** Reconcile one window's owner presence with the agents poll (CMX-427). */
export function syncOwnerPresence(wid: string, shared: boolean, epoch: number | null | undefined): Promise<unknown>;
/** Wire the one shim<->parent postMessage router. Idempotent. */
export function initOwnerPresence(): void;
/** A share minted or adopted on this page: start presence with the code already in hand. */
export function startOwnerPresence(wid: string, joinUrl: string | undefined, code: string | undefined, epoch: number | null | undefined): Promise<unknown>;
/** A share stopped, or its pane killed. */
export function stopOwnerPresence(wid: string): void;
