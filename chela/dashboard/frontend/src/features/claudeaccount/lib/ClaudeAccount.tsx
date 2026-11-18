// CMX-395: the Claude account the CLI is signed in to, under the sidebar head: the email (and
// org), hover for the plan, the login method and the expiry. Read on load (a fresh server
// read) and then every ACCOUNT_POLL_MS, never per tick: the server caches the CLI's answer.
import { api } from '../../../shared/api/index.ts';
import { icons } from '../../../shared/icons/index.ts';
import { registerLane, type Tick } from '../../../shared/scheduler/index.ts';
import { createStore, useStore } from '../../../shared/store/index.ts';
import { accountView } from '../model.ts';

/** The last /api/claude-account answer; undefined until the first one lands. */
export const claudeAccount = createStore<unknown>(undefined);

/** Every five minutes, the same period as the server's cache. */
export const ACCOUNT_POLL_MS = 5 * 60_000;

let lastRead = -Infinity;

/** Forget the last read (tests, and nothing else). */
export function resetClaudeAccount(): void {
  lastRead = -Infinity;
  claudeAccount.set(undefined);
}

/** Never throws out. A failed read keeps the last answer; with none yet it is unknown, never
 * an empty line that reads as fine. */
export async function tickClaudeAccount(fresh = false): Promise<void> {
  try {
    const answer = await api(`/api/claude-account${fresh ? '?fresh=1' : ''}`);
    claudeAccount.set(answer ?? null);
  } catch (e) {
    console.error('tickClaudeAccount', e);
    if (claudeAccount.get() === undefined) {
      claudeAccount.set({ state: 'unknown', reason: 'the dashboard did not answer' });
    }
  }
}

/** The lane: the boot tick reads fresh; later ticks read only once ACCOUNT_POLL_MS has passed. */
export function accountLane(t: Pick<Tick, 'boot'>, now: number = Date.now()): Promise<void> | undefined {
  if (!t.boot && now - lastRead < ACCOUNT_POLL_MS) return undefined;
  lastRead = now;
  return tickClaudeAccount(t.boot);
}

export function ClaudeAccount() {
  const v = accountView(useStore(claudeAccount));
  const Icon = v.tone === 'ok' || v.tone === 'pending' ? icons.account : icons.warn;
  return (
    <div className={`claude-account ca-${v.tone}`} id="claude-account" title={v.title} data-tone={v.tone}>
      <Icon size={13} aria-hidden="true" />
      <span className="ca-text">{v.text}</span>
      {v.warn && <span className="ca-warn">{v.warn}</span>}
    </div>
  );
}

// detached: a cold server read can take seconds, and must not hold up the tick
registerLane('claudeAccount', (t) => accountLane(t), 'detached');
