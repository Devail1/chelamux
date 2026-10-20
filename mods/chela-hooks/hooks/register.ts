// SPIKE (CMX-157): chela's in-session hook layer as a Claude Code Mod — the merge gate
// and event posting, side by side with the shell-hook plugin in `plugin/`. Nothing here
// replaces that plugin; docs/MODS_SPIKE.md holds the measurements and the go/no-go.
//
// * Event posting: the same POST `/hooks/<Event>` with the same body the shell plugin's
//   curl sends (the settings hook's stdin), so `chela.hooks.ingest` reads it unchanged.
//   It goes through `$.http.fetch` — no process per event — and is NOT awaited: the agent
//   never waits on the daemon. Gated on `$CHELA_WID`, as CMX-319 gates the shell hooks.
// * Merge gate: the decision stays in `plugin/hooks/mergegate.py` (one parser, not two);
//   this hook feeds it the same payload and relays its deny reason verbatim. Like the
//   shell hook it fails OPEN when the gate cannot decide.
import type { Register } from 'claude-code'
import { refresh } from './status'
import { band, onDisplay } from './timestamps'
import { band as needsBand, start as needsStart } from './needsyou'

const RESERVED = new Set(['tool', 'tool_use_id', 'consent', 'agentId', 'requestMeta'])

type Body = Record<string, unknown>

// `classic.PreToolUse` carries no base fields; the last transcript path any other event
// carried stands in (ingest falls back to the session id when it is empty).
let transcript = ''

async function post($: any, event: string, body: Body, extra: Record<string, string> = {}) {
  // CMX-181: opt-in by URL. In a chela pane plugin/ already posts: a default would file each twice.
  const wid = await $.env.get('CHELA_WID')
  const base = await $.env.get('CHELA_MOD_HOOKS_URL')
  if (!wid || !base) return
  const headers: Record<string, string> = {
    'Content-Type': 'application/json', 'X-Chela-Mod-T0': String(Date.now()), ...extra,
  }
  if (event === 'SessionStart') headers['X-Chela-Wid'] = wid
  await $.http.fetch(`${base}/hooks/${event}`, {
    method: 'POST', headers, body: JSON.stringify(body),
  }).catch(() => undefined)                // a dead daemon is a lost event, never a stall
}

// The classic events hand over the settings hook's own stdin, so the body is `e` as is.
async function relay($: any, e: any) {
  if (typeof e.transcript_path === 'string' && e.transcript_path) transcript = e.transcript_path
  await post($, String(e.hook_event_name), e as Body)
}

// The deny reason mergegate.py printed, or '' to allow. Anything it cannot decide — the
// script missing, a crash, a timeout, output that is not its deny shape — allows, exactly
// as the shell hook's `|| true` does.
async function gate($: any, payload: Body): Promise<string> {
  const script = (await $.env.get('CHELA_MOD_MERGEGATE'))
    || `${$.plugin.root}/../../plugin/hooks/mergegate.py`
  try {
    const ran = await $.process.run(['python3', '-I', '-S', script],
      { stdin: JSON.stringify(payload), timeoutMs: 5000 })
    if (ran.exitCode !== 0 || !ran.stdout.trim()) return ''
    const out = JSON.parse(ran.stdout).hookSpecificOutput
    return out?.permissionDecision === 'deny' ? String(out.permissionDecisionReason ?? '') : ''
  } catch {
    return ''
  }
}

export const register: Register = (on, options) => {
  transcript = ''
  // the status line (status.ts): drawn at start, after every turn and after a compaction
  on('session.start', ($, e, next) => { void refresh($, options); void needsStart($); return next(e) })
  on('turn.complete', ($, e, next) => { void refresh($, options); return next(e) })
  on('session.compact', async ($, e, next) => {
    const done = await next(e)
    void refresh($, options)
    return done
  })
  // the turn-timestamp band (timestamps.tsx), and its handshake with the plugin/ hook
  on('classic.MessageDisplay', ($, e, next) => onDisplay($, e, next))
  on('ui.render', { component: 'AbovePrompt' }, ($, e, next) => band($, e, next))
  // the "needs you" band (needsyou.tsx), orchestrator session only, polled from the daemon
  on('ui.render', { component: 'AbovePrompt' }, ($, e, next) => needsBand($, e, next))
  on('classic.SessionStart', ($, e, next) => { void relay($, e); return next(e) })
  on('classic.SessionEnd', ($, e, next) => { void relay($, e); return next(e) })
  on('classic.Stop', ($, e, next) => { void relay($, e); return next(e) })
  on('classic.PostToolUse', ($, e, next) => { void relay($, e); return next(e) })
  on('classic.PermissionDenied', ($, e, next) => { void relay($, e); return next(e) })

  // PreToolUse rides `tool.call`, not `classic.PreToolUse`: the classic event fires BENEATH
  // every tool.call hook, so a call the gate denies would never be posted — the shell
  // plugin posts it (its ingestion hook and gate run side by side).
  on('tool.call', async ($, e, next) => {
    const t0 = Date.now()
    const tool_input: Body = {}
    for (const [k, v] of Object.entries(e)) if (!RESERVED.has(k)) tool_input[k] = v
    const body: Body = {
      session_id: await $.session.id(), transcript_path: transcript,
      cwd: await $.session.cwd(), hook_event_name: 'PreToolUse',
      tool_name: e.tool, tool_input, tool_use_id: e.tool_use_id ?? '',
    }
    const reason = e.tool === 'Bash' ? await gate($, body) : ''
    // what this hook cost the agent before the tool ran — the number MODS_SPIKE.md compares
    void post($, 'PreToolUse', body, { 'X-Chela-Mod-Pre-Ms': String(Date.now() - t0) })
    return reason ? { deny: reason } : next(e)
  })
}
