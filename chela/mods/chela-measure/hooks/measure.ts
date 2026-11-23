// CMX-477: chela's usage feed from `session.measure`, the figures Claude Code's own status
// line gets (context fill, the 5h/7d plan limits, cost), pushed after each main-thread turn
// and when a limit moves a whole point. It writes them, in the statusLine payload's shape,
// to $CHELA_DIR/context/mod/<session_id>.json, tagged `source: "mod"`. chela.context and
// chela.usage read that file beside the statusLine's own and take the fresher one.
//
// It shares nothing with other mods: its own name, no `$.state`, no `$.store`, one file per
// session under chela's own directory. register.ts passes every event on (`next(e)` first),
// so a mod beneath this one still gets it.
import type { SessionMeasureInput } from 'claude-code'

// The shape scripts/cache-statusline.sh and chela.sessions.SESSION_RE accept for a session
// id that becomes a path component. Anything else writes nothing.
export const SESSION_RE = /^[0-9a-fA-F][0-9a-fA-F-]{7,63}$/

// What `chela install-mod` writes into the installed copy: the CHELA_DIR it ran with.
export const CONFIG_FILE = 'chela.json'

// The statusLine payload chela.context._parse_cache_file reads, from one measurement.
// A figure the engine does not have is left out, never zeroed.
export function snapshot(ids: { session_id: string, cwd: string, model: string },
                         e: SessionMeasureInput) {
  const ctx: Record<string, number> = { context_window_size: e.context.window }
  if (typeof e.context.tokens === 'number') ctx.total_input_tokens = e.context.tokens
  if (typeof e.context.percent === 'number') {
    ctx.used_percentage = e.context.percent
    ctx.remaining_percentage = 100 - e.context.percent
  }
  const rate_limits: Record<string, Record<string, number>> = {}
  for (const r of e.rateLimits) {
    const at = Date.parse(r.resetsAt ?? '')
    rate_limits[r.kind] = Number.isNaN(at) ? { used_percentage: r.percentUsed }
      : { used_percentage: r.percentUsed, resets_at: Math.floor(at / 1000) }
  }
  return {
    source: 'mod', session_id: ids.session_id, cwd: ids.cwd, workspace: { current_dir: ids.cwd },
    model: { id: ids.model, display_name: ids.model }, context_window: ctx, rate_limits,
    ...(e.cost ? { cost: { total_cost_usd: e.cost.usd } } : {}),
  }
}

// $CHELA_DIR in the session's environment, else the one `chela install-mod` recorded, else
// ~/.chela. '' when none can be named (no HOME): then nothing is written.
export async function chelaDir($: any): Promise<string> {
  const env = ((await $.env.get('CHELA_DIR')) || '').trim()
  if (env) return env
  try {
    const recorded = JSON.parse(await $.fs.read(`${$.plugin.root}/${CONFIG_FILE}`))?.chela_dir
    if (typeof recorded === 'string' && recorded.trim()) return recorded.trim()
  } catch {
    // not installed by chela install-mod (a --plugin-dir run): the default below
  }
  const home = ((await $.env.get('HOME')) || '').trim()
  return home ? `${home}/.chela` : ''
}

// One measurement to its file. A failure is a lost sample, never a failed turn.
export async function record($: any, e: SessionMeasureInput): Promise<void> {
  try {
    const session_id = await $.session.id()
    if (typeof session_id !== 'string' || !SESSION_RE.test(session_id)) return
    const dir = await chelaDir($)
    if (!dir) return
    const body = snapshot({ session_id, cwd: await $.session.cwd(), model: await $.session.model() }, e)
    await $.fs.write(`${dir}/context/mod/${session_id}.json`, JSON.stringify(body))
  } catch {
    // the reader falls back to the statusLine's file, else the transcript
  }
}
