// CMX-219 (CMX-212 P20/P21): the last /api/agents/context read, one row per agent (context %,
// model, cost, and the account-wide 5h / 7d rate-limit samples). The refresh loop's one read
// per tick lands here (the gear menu's readouts register that lane); the agent detail reads it.
import { api } from '../api/index.ts';
import { createStore } from '../bridge/index.ts';

export type ContextRow = {
  name?: string;
  window_id?: string;
  ts?: string;
  used?: number | string | null;
  total?: number | string | null;
  used_pct?: number | null;
  estimated?: boolean;
  model?: string | null;
  cost_usd?: number | null;
  session_name?: string | null;
  rate_limit_pct?: number | null;
  rate_limit_resets_at?: number | null;
  weekly_rl_pct?: number | null;
  weekly_rl_resets_at?: number | null;
};

export const context = createStore<ContextRow[]>([]);

/** Publish `read` (the tick's shared read), or a fresh /api/agents/context read. A failed or
 * non-list answer keeps the last rows. */
export async function loadContext(read?: Promise<unknown> | null): Promise<void> {
  const data = await (read ?? api('/api/agents/context'));
  if (Array.isArray(data)) context.set(data as ContextRow[]);
}
