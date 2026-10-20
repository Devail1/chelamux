// The /api/agents rows legacy last read (util.js `_agentsCache`). The bridge's own store, so
// the islands and legacy share one value: `installBridge` returns the page's one bridge.
import { installBridge } from '../bridge/index.ts';

export type { Agent } from '../bridge/index.ts';
export const agents = installBridge(window).agents;
